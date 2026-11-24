import base64
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError

PATH = Path(__file__).resolve().parents[1] / "scripts" / "heartbeat.py"
SPEC = importlib.util.spec_from_file_location("heartbeat", PATH)
heartbeat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(heartbeat)

NOW = datetime(2026, 11, 24, 20, 0, tzinfo=timezone.utc)


def stamp(instant):
    return instant.isoformat(timespec="seconds").replace("+00:00", "Z")


class FakeGitHub:
    def __init__(self, age=31, previous=None):
        self.commit = NOW - timedelta(days=age)
        self.previous = previous
        self.sha = "a" * 40
        self.calls = []
        self.history_status = 200
        self.empty = False
        self.conflict_once = False
        self.always_conflict = False
        self.write_status = 201

    @property
    def writes(self):
        return [call for call in self.calls if call[0] == "PUT"]

    def api(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET" and "/commits?" in path:
            if self.history_status != 200:
                return self.history_status, None
            return 200, [] if self.empty else [{"commit": {"committer": {"date": stamp(self.commit)}}}]
        if method == "GET":
            if self.previous is None:
                return 404, None
            return 200, {"type": "file", "encoding": "base64", "sha": self.sha,
                         "content": base64.b64encode(json.dumps(self.previous).encode()).decode()}
        if self.conflict_once:
            self.conflict_once = False
            self.commit = NOW
            self.previous = {"schema": 1, "lastHeartbeatUtc": stamp(NOW)}
            self.sha = "b" * 40
            return 409, None
        if self.always_conflict:
            return 409, None
        if self.write_status not in (200, 201):
            return self.write_status, None
        self.previous = json.loads(base64.b64decode(payload["content"]))
        self.commit = heartbeat.utc(self.previous["lastHeartbeatUtc"])
        self.sha = "c" * 40
        return self.write_status, {"commit": {"sha": self.sha}}


class HeartbeatTests(unittest.TestCase):
    def maintain(self, api, **kwargs):
        return heartbeat.maintain(api, heartbeat.REPOSITORY, "main", now=NOW, **kwargs)

    def test_thirty_day_boundary_is_exact(self):
        self.assertFalse(heartbeat.due(NOW - timedelta(days=30) + timedelta(seconds=1), None, NOW))
        self.assertTrue(heartbeat.due(NOW - timedelta(days=30), None, NOW))

    def test_recent_repository_without_heartbeat_does_not_always_commit(self):
        api = FakeGitHub(age=2)
        self.assertEqual(self.maintain(api), {"updated": False, "reason": "recent_repository_activity"})
        self.assertEqual(self.maintain(api)["updated"], False)
        self.assertEqual(api.writes, [])

    def test_recent_normal_commit_postpones_old_heartbeat(self):
        api = FakeGitHub(age=5, previous={"schema": 1, "lastHeartbeatUtc": stamp(NOW - timedelta(days=45))})
        self.assertFalse(self.maintain(api)["updated"])
        self.assertEqual(api.writes, [])

    def test_recent_heartbeat_postpones_old_commit(self):
        api = FakeGitHub(age=50, previous={"schema": 1, "lastHeartbeatUtc": stamp(NOW - timedelta(days=10))})
        self.assertFalse(self.maintain(api)["updated"])
        self.assertEqual(api.writes, [])

    def test_empty_repository_is_skipped_without_initial_or_repeated_commits(self):
        for status in (200, 409):
            api = FakeGitHub()
            api.empty, api.history_status = True, status
            self.assertEqual(self.maintain(api), {"updated": False, "reason": "repository_has_no_commits"})
            self.assertEqual(api.writes, [])

    def test_first_old_repository_heartbeat_writes_exactly_one_safe_file(self):
        api = FakeGitHub()
        result = self.maintain(api)
        self.assertTrue(result["updated"])
        self.assertEqual(len(api.writes), 1)
        _method, path, payload = api.writes[0]
        self.assertEqual(path, "/repos/" + heartbeat.REPOSITORY + "/contents/" + heartbeat.HEARTBEAT_PATH)
        self.assertEqual(set(api.previous), {"schema", "lastHeartbeatUtc"})
        self.assertEqual(api.previous["lastHeartbeatUtc"], stamp(NOW))
        self.assertEqual(payload["committer"], heartbeat.BOT)
        self.assertNotIn("sha", payload)
        self.assertNotIn("donors", json.dumps(payload))

    def test_existing_old_heartbeat_uses_sha_and_preserves_other_repository_paths(self):
        api = FakeGitHub(previous={"schema": 1, "lastHeartbeatUtc": stamp(NOW - timedelta(days=35))})
        self.maintain(api)
        self.assertEqual(api.writes[0][2]["sha"], "a" * 40)
        self.assertEqual([path for method, path, _payload in api.calls if method != "GET"],
                         ["/repos/" + heartbeat.REPOSITORY + "/contents/" + heartbeat.HEARTBEAT_PATH])

    def test_next_run_after_commit_is_a_noop(self):
        api = FakeGitHub()
        self.assertTrue(self.maintain(api)["updated"])
        original = dict(api.previous)
        result = heartbeat.maintain(api, heartbeat.REPOSITORY, "main", now=NOW + timedelta(minutes=5))
        self.assertFalse(result["updated"])
        self.assertEqual(len(api.writes), 1)
        self.assertEqual(api.previous, original)

    def test_concurrent_success_is_reread_and_not_committed_again(self):
        api = FakeGitHub()
        api.conflict_once = True
        self.assertFalse(self.maintain(api)["updated"])
        self.assertEqual(len(api.writes), 1)  # Our first write conflicted; another actor supplied the fresh heartbeat.
        self.assertEqual(sum(1 for method, path, _ in api.calls if method == "GET" and "/commits?" in path), 2)

    def test_repeated_conflicts_are_bounded_and_fail_for_next_schedule(self):
        api = FakeGitHub()
        api.always_conflict = True
        with self.assertRaises(heartbeat.HeartbeatError):
            self.maintain(api)
        self.assertEqual(len(api.writes), 2)

    def test_check_only_never_writes(self):
        api = FakeGitHub()
        self.assertEqual(self.maintain(api, check_only=True), {"updated": False, "reason": "maintenance_commit_due", "checkOnly": True})
        self.assertEqual(api.writes, [])

    def test_branch_protection_or_bad_permissions_are_not_bypassed(self):
        api = FakeGitHub()
        api.write_status = 403
        with self.assertRaises(heartbeat.HeartbeatError):
            self.maintain(api)
        self.assertEqual(len(api.writes), 1)
        api = FakeGitHub()
        api.history_status = 404
        with self.assertRaises(heartbeat.HeartbeatError):
            self.maintain(api)
        self.assertEqual(api.writes, [])

    def test_invalid_heartbeat_or_future_dates_fail_without_write(self):
        for previous in ({"schema": True, "lastHeartbeatUtc": stamp(NOW)}, {"schema": 1, "lastHeartbeatUtc": "invalid"},
                         {"schema": 1, "lastHeartbeatUtc": stamp(NOW), "unrelated": "redacted"},
                         {"schema": 1, "lastHeartbeatUtc": stamp(NOW + timedelta(days=1))}):
            api = FakeGitHub(previous=previous)
            with self.subTest(previous=previous), self.assertRaises(heartbeat.HeartbeatError):
                self.maintain(api)
            self.assertEqual(api.writes, [])

    def test_wrong_repository_is_rejected_before_api_access(self):
        api = FakeGitHub()
        with self.assertRaises(heartbeat.HeartbeatError):
            heartbeat.maintain(api, "unrelated/repository", "main", now=NOW)
        self.assertEqual(api.calls, [])

    def test_missing_write_token_fails_before_network(self):
        class FailIfCalled:
            def open(self, *_args, **_kwargs):
                raise AssertionError("Unexpected network access")
        api = heartbeat.GitHub(opener=FailIfCalled())
        with self.assertRaises(heartbeat.HeartbeatError):
            api.api("PUT", "/repos/" + heartbeat.REPOSITORY + "/contents/" + heartbeat.HEARTBEAT_PATH, {})

    def test_ephemeral_token_is_scoped_to_header_and_never_echoed_on_failure(self):
        token = "test-job-token-never-committed"
        class Reject:
            captured = None
            def open(self, req, timeout):
                self.captured = req
                raise HTTPError(req.full_url, 403, "Rejected", None, None)
        opener = Reject()
        api = heartbeat.GitHub(token, opener)
        path = "/repos/" + heartbeat.REPOSITORY + "/contents/" + heartbeat.HEARTBEAT_PATH
        status, response = api.api("PUT", path, {"content": "timestamp-data-only"})
        self.assertEqual((status, response), (403, None))
        self.assertEqual(opener.captured.get_header("Authorization"), "Bearer " + token)
        self.assertNotIn(token, opener.captured.full_url)
        self.assertNotIn(token.encode(), opener.captured.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
