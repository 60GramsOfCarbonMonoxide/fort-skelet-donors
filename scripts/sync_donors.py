"""Validate the public donor feed and prepare a GitHub Pages artifact.

No credentials, database access, private Ko-fi records, dynamic code execution,
repository writes, or third-party Python packages are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
import json
from pathlib import Path
import sys
import unicodedata
from urllib import error, parse, request

SOURCE_URL = "https://zfgdvgrvuocshtxrclpj.supabase.co/functions/v1/fort-kofi/donors.json"
PUBLISHED_URL = "https://60gramsofcarbonmonoxide.github.io/fort-skelet-donors/donors.json"
MAX_BYTES = 512 * 1024
MAX_DONORS = 5000
MAX_NAME_UTF16 = 100
FEED_KEYS = frozenset({"schema", "generatedUtc", "donors"})


class FeedError(ValueError):
    pass


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FeedError("The feed contains duplicate JSON keys.")
        result[key] = value
    return result


def invalid_constant(_value):
    raise FeedError("The feed contains a non-JSON numeric value.")


def valid_utc(value):
    if not isinstance(value, str) or not 20 <= len(value) <= 40:
        raise FeedError("generatedUtc must be an ISO 8601 UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeedError("generatedUtc must be an ISO 8601 UTC timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise FeedError("generatedUtc must use UTC.")
    return value


def parse_feed(body):
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise FeedError("The feed exceeds the 512 KiB limit.")
    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=no_duplicate_keys, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise FeedError("The feed is not a valid UTF-8 JSON document.") from exc
    if not isinstance(data, dict) or set(data) != FEED_KEYS:
        raise FeedError("The public feed must contain exactly schema, generatedUtc and donors.")
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise FeedError("Unsupported donor feed schema.")
    valid_utc(data["generatedUtc"])
    donors = data["donors"]
    if not isinstance(donors, list) or len(donors) > MAX_DONORS:
        raise FeedError("The feed must contain at most 5,000 donor names.")
    for name in donors:
        if not isinstance(name, str) or not name or name != name.strip():
            raise FeedError("The feed contains an invalid donor display name.")
        try:
            units = len(name.encode("utf-16-le")) // 2
        except UnicodeEncodeError as exc:
            raise FeedError("The feed contains invalid Unicode.") from exc
        if units > MAX_NAME_UTF16 or any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in name):
            raise FeedError("The feed contains an invalid donor display name.")
    # Display names are exact values: preserve case and Unicode; only deduplicate
    # identical strings and sort them for stable semantic comparisons.
    return {"schema": 1, "generatedUtc": data["generatedUtc"], "donors": sorted(set(donors))}


def validate_url(url, source=False):
    value = parse.urlsplit(url)
    if value.scheme != "https" or value.username or value.password or value.query or value.fragment:
        raise FeedError("Only the configured public HTTPS feed URLs are allowed.")
    if source:
        if value.netloc.lower() != "zfgdvgrvuocshtxrclpj.supabase.co" or value.path != "/functions/v1/fort-kofi/donors.json":
            raise FeedError("Unexpected source feed URL.")
    elif value.netloc.lower() != "60gramsofcarbonmonoxide.github.io" or value.path != "/fort-skelet-donors/donors.json":
        raise FeedError("Unexpected previously published feed URL.")
    return url


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        return None


def download(url, missing_ok=False, source=False, opener=None):
    validate_url(url, source=source)
    client = opener or request.build_opener(NoRedirect())
    req = request.Request(url, headers={"Accept": "application/json", "User-Agent": "Fort-Skelet-Public-Donor-Sync/1"})
    try:
        with client.open(req, timeout=20) as response:
            if response.status != HTTPStatus.OK:
                raise FeedError("A public feed returned a non-success status.")
            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise FeedError("A public feed did not return application/json.")
            body = response.read(MAX_BYTES + 1)
    except error.HTTPError as exc:
        if missing_ok and exc.code == HTTPStatus.NOT_FOUND:
            return None
        raise FeedError("A public feed could not be fetched (HTTP " + str(exc.code) + ").") from None
    except (error.URLError, TimeoutError, OSError) as exc:
        raise FeedError("A public feed is unavailable; the existing Pages deployment is preserved.") from exc
    return parse_feed(body)


def prepare(current, previous, now=None):
    changed = previous is None or current["donors"] != previous["donors"]
    if changed:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset().total_seconds() != 0:
            raise FeedError("Publication time must use UTC.")
        generated = instant.isoformat(timespec="seconds").replace("+00:00", "Z")
    else:
        # Source timestamps can advance on every query. They must not create a
        # deployment or change the last successful publication's timestamp.
        generated = previous["generatedUtc"]
    return changed, {"schema": 1, "generatedUtc": generated, "donors": current["donors"]}


def sync(source_url, published_url, output, github_output=None, fetch=download, now=None):
    current = fetch(source_url, source=True)
    previous = fetch(published_url, missing_ok=True)
    changed, public = prepare(current, previous, now=now)
    encoded = (json.dumps(public, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise FeedError("The normalized public feed exceeds the 512 KiB limit.")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(destination.name + ".pending")
    pending.write_bytes(encoded)
    pending.replace(destination)
    if github_output is not None:
        # Only a locally computed boolean is written to workflow output syntax.
        # No remote string or donor name is interpolated into a command/output.
        with Path(github_output).open("a", encoding="utf-8") as stream:
            stream.write("changed=" + ("true" if changed else "false") + "\n")
    return changed, len(public["donors"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SOURCE_URL)
    parser.add_argument("--previous", default=PUBLISHED_URL)
    parser.add_argument("--output", default="_site/donors.json")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    changed, count = sync(args.source, args.previous, args.output, args.github_output)
    print("Public donor feed: " + ("changed; deployment required" if changed else "unchanged; deployment skipped") + "; " + str(count) + " names.")


if __name__ == "__main__":
    try:
        main()
    except (FeedError, OSError) as exc:
        print("Donor feed sync refused: " + str(exc), file=sys.stderr)
        sys.exit(1)
