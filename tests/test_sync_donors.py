from datetime import datetime, timezone
from email.message import Message
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "sync_donors.py"
SPEC = importlib.util.spec_from_file_location("sync_donors", MODULE)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)

OLD = "2026-11-20T12:00:00Z"
SOURCE_TIME = "2026-11-24T15:00:00Z"
NOW = datetime(2026, 11, 24, 17, 30, 0, tzinfo=timezone.utc)


def body(names=None, timestamp=SOURCE_TIME, **changes):
    item = {"schema": 1, "generatedUtc": timestamp, "donors": [] if names is None else names}
    item.update(changes)
    return json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Response:
    def __init__(self, payload, content_type="application/json", status=200):
        self.payload, self.status = payload, status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.requested_size = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, size):
        self.requested_size = size
        return self.payload[:size]


class Opener:
    def __init__(self, response=None, failure=None):
        self.response, self.failure = response, failure
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.failure:
            raise self.failure
        return self.response


class FeedTests(unittest.TestCase):
    def test_valid_feed_deduplicates_sorts_and_preserves_exact_names(self):
        result = sync.parse_feed(body(["Zulu", "Comrade \u041c\u043e\u0440\u043e\u0437", "Alpha", "Alpha", "alpha"]))
        self.assertEqual(result["donors"], ["Alpha", "Comrade \u041c\u043e\u0440\u043e\u0437", "Zulu", "alpha"])
        self.assertEqual(set(result), {"schema", "generatedUtc", "donors"})

    def test_wrong_schema_never_accepts_boolean_or_float(self):
        for value in (True, False, 1.0, "1", 2, None):
            with self.subTest(schema=value), self.assertRaises(sync.FeedError):
                sync.parse_feed(body(schema=value))

    def test_public_keys_are_an_exact_allowlist(self):
        for key in ("email", "verification_token", "roles", "payments"):
            with self.subTest(key=key), self.assertRaises(sync.FeedError):
                sync.parse_feed(body(**{key: "redacted"}))
        with self.assertRaises(sync.FeedError):
            sync.parse_feed(b'{"schema":1,"donors":[]}')

    def test_duplicate_json_keys_and_non_json_numbers_are_rejected(self):
        with self.assertRaises(sync.FeedError):
            sync.parse_feed(b'{"schema":1,"schema":1,"generatedUtc":"2026-11-24T00:00:00Z","donors":[]}')
        with self.assertRaises(sync.FeedError):
            sync.parse_feed(b'{"schema":NaN,"generatedUtc":"2026-11-24T00:00:00Z","donors":[]}')

    def test_input_byte_and_raw_name_count_limits(self):
        with self.assertRaises(sync.FeedError):
            sync.parse_feed(b" " * (sync.MAX_BYTES + 1))
        with self.assertRaises(sync.FeedError):
            sync.parse_feed(body(["One"] * (sync.MAX_DONORS + 1)))

    def test_invalid_names_are_rejected_without_echoing_remote_data(self):
        for name in ("", " Leading", "Trailing ", "Bad\nName", "Bad\x00Name", "Bad\u2028Name", "a" * 101, 2, None):
            with self.subTest(name=name), self.assertRaises(sync.FeedError) as caught:
                sync.parse_feed(body([name]))
            self.assertNotIn("Bad", str(caught.exception))

    def test_utf16_name_limit_matches_udon_and_lone_surrogates_fail(self):
        self.assertEqual(len(sync.parse_feed(body(["\U0001f30d" * 50]))["donors"]), 1)
        with self.assertRaises(sync.FeedError):
            sync.parse_feed(body(["\U0001f30d" * 51]))
        with self.assertRaises(sync.FeedError):
            sync.parse_feed(b'{"schema":1,"generatedUtc":"2026-11-24T00:00:00Z","donors":["\\ud800"]}')

    def test_generated_time_requires_valid_utc(self):
        for value in (None, 4, "invalid", "2026-11-24T12:00:00", "2026-11-24T12:00:00+02:00", "2026-99-99T12:00:00Z"):
            with self.subTest(timestamp=value), self.assertRaises(sync.FeedError):
                sync.parse_feed(body(timestamp=value))

    def test_unchanged_semantics_preserve_previous_generated_time(self):
        current = sync.parse_feed(body(["Beta", "Alpha", "Alpha"]))
        previous = sync.parse_feed(body(["Alpha", "Beta"], timestamp=OLD))
        changed, result = sync.prepare(current, previous, now=NOW)
        self.assertFalse(changed)
        self.assertEqual(result["generatedUtc"], OLD)
        self.assertEqual(result["donors"], ["Alpha", "Beta"])

    def test_changed_membership_regenerates_publication_time(self):
        changed, result = sync.prepare(sync.parse_feed(body(["New"])), sync.parse_feed(body(["Old"], timestamp=OLD)), now=NOW)
        self.assertTrue(changed)
        self.assertEqual(result["generatedUtc"], "2026-11-24T17:30:00Z")
        self.assertEqual(result["donors"], ["New"])

    def test_initial_missing_page_deploys_even_an_empty_valid_list(self):
        changed, result = sync.prepare(sync.parse_feed(body()), None, now=NOW)
        self.assertTrue(changed)
        self.assertEqual(result["donors"], [])

    def test_only_previous_page_404_is_bootstrap_not_source_404(self):
        missing = HTTPError(sync.PUBLISHED_URL, 404, "Not found", None, None)
        self.assertIsNone(sync.download(sync.PUBLISHED_URL, missing_ok=True, opener=Opener(failure=missing)))
        with self.assertRaises(sync.FeedError):
            sync.download(sync.SOURCE_URL, source=True, opener=Opener(failure=HTTPError(sync.SOURCE_URL, 404, "Not found", None, None)))

    def test_bad_http_content_or_redirect_cannot_become_a_feed(self):
        for response in (Response(body(), content_type="text/html"), Response(body(), status=503)):
            with self.subTest(response=response), self.assertRaises(sync.FeedError):
                sync.download(sync.SOURCE_URL, source=True, opener=Opener(response=response))
        for code in (301, 401, 500):
            opener = Opener(failure=HTTPError(sync.SOURCE_URL, code, "Request failed", None, None))
            with self.subTest(code=code), self.assertRaises(sync.FeedError):
                sync.download(sync.SOURCE_URL, source=True, opener=opener)
            self.assertEqual(len(opener.requests), 1)

    def test_download_is_bounded_and_sends_no_authentication(self):
        response = Response(body())
        opener = Opener(response=response)
        sync.download(sync.SOURCE_URL, source=True, opener=opener)
        self.assertEqual(response.requested_size, sync.MAX_BYTES + 1)
        req, timeout = opener.requests[0]
        self.assertEqual(timeout, 20)
        self.assertFalse(req.has_header("Authorization"))
        with self.assertRaises(sync.FeedError):
            sync.download("https://unexpected.invalid/feed.json", source=True, opener=opener)

    def test_source_or_previous_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            target, output = Path(folder) / "donors.json", Path(folder) / "workflow-output"
            target.write_text("last good artifact", encoding="utf-8")
            for fail_previous in (False, True):
                def fetch(_url, **kwargs):
                    if not fail_previous or kwargs.get("missing_ok"):
                        raise sync.FeedError("Simulated network failure")
                    return sync.parse_feed(body(["Valid"]))
                with self.subTest(previous=fail_previous), self.assertRaises(sync.FeedError):
                    sync.sync(sync.SOURCE_URL, sync.PUBLISHED_URL, target, output, fetch=fetch, now=NOW)
                self.assertEqual(target.read_text(), "last good artifact")
                self.assertFalse(output.exists())

    def test_sync_writes_data_only_and_a_controlled_boolean_output(self):
        literal = "__import__('os').system('this_is_only_a_name')"
        current = sync.parse_feed(body([literal, "Comrade"]))
        with tempfile.TemporaryDirectory() as folder:
            target, output = Path(folder) / "site" / "donors.json", Path(folder) / "workflow-output"
            def fetch(_url, **kwargs):
                return None if kwargs.get("missing_ok") else current
            changed, count = sync.sync(sync.SOURCE_URL, sync.PUBLISHED_URL, target, output, fetch=fetch, now=NOW)
            self.assertTrue(changed)
            self.assertEqual(count, 2)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["donors"], sorted([literal, "Comrade"]))
            self.assertEqual(output.read_text(), "changed=true\n")
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["site", "workflow-output"])

    def test_pretty_encoded_artifact_cannot_exceed_public_byte_limit(self):
        names = [str(i).zfill(4) + "x" * 96 for i in range(5000)]
        self.assertLess(len(body(names)), sync.MAX_BYTES)
        current = sync.parse_feed(body(names))
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "donors.json"
            def fetch(_url, **kwargs):
                return None if kwargs.get("missing_ok") else current
            with self.assertRaises(sync.FeedError):
                sync.sync(sync.SOURCE_URL, sync.PUBLISHED_URL, destination, fetch=fetch, now=NOW)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
