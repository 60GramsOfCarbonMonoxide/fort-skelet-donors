"""Make one timestamp-only maintenance commit after 30 days without commits.

Uses only the job's short-lived GITHUB_TOKEN. The sole writable path is a public
heartbeat record; donor feeds, workflow/source files and git credentials are not
modified. The GitHub Contents API preserves the rest of the branch tree.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sys
from urllib import error, parse, request

REPOSITORY = "60GramsOfCarbonMonoxide/fort-skelet-donors"
HEARTBEAT_PATH = ".github/donor-feed-heartbeat.json"
INTERVAL = timedelta(days=30)
BOT = {"name": "github-actions[bot]", "email": "41898282+github-actions[bot]@users.noreply.github.com"}
MAX_API_BYTES = 128 * 1024


class HeartbeatError(ValueError):
    pass


def utc(value):
    if not isinstance(value, str) or not 20 <= len(value) <= 40:
        raise HeartbeatError("A repository activity timestamp is missing or invalid.")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HeartbeatError("A repository activity timestamp is invalid.") from exc
    if instant.tzinfo is None or instant.utcoffset().total_seconds() != 0:
        raise HeartbeatError("Repository activity timestamps must use UTC.")
    return instant


def unique_keys(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise HeartbeatError("The heartbeat record contains duplicate fields.")
        result[name] = value
    return result


def parse_record(encoded):
    if not isinstance(encoded, str) or len(encoded) > 4096:
        raise HeartbeatError("The heartbeat record is invalid or oversized.")
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HeartbeatError("The heartbeat record is not valid base64 JSON.") from exc
    if not isinstance(record, dict) or set(record) != {"schema", "lastHeartbeatUtc"} or type(record["schema"]) is not int or record["schema"] != 1:
        raise HeartbeatError("The heartbeat record has an unsupported schema.")
    return utc(record["lastHeartbeatUtc"])


def due(last_commit, previous_heartbeat, now):
    if now.tzinfo is None or now.utcoffset().total_seconds() != 0:
        raise HeartbeatError("The current time must use UTC.")
    if last_commit is None:
        return False  # An empty repository cannot run a scheduled workflow.
    activity = max(instant for instant in (last_commit, previous_heartbeat) if instant is not None)
    if activity > now + timedelta(minutes=5):
        raise HeartbeatError("A repository activity timestamp is unexpectedly in the future.")
    return now - activity >= INTERVAL


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        return None


class GitHub:
    def __init__(self, token="", opener=None):
        self.token = token
        self.opener = opener or request.build_opener(NoRedirect())

    def api(self, method, path, payload=None):
        if not path.startswith("/repos/" + REPOSITORY + "/"):
            raise HeartbeatError("Unexpected maintenance API target.")
        headers = {"Accept": "application/vnd.github+json", "Content-Type": "application/json",
                   "User-Agent": "Fort-Skelet-Donor-Heartbeat/1", "X-GitHub-Api-Version": "2026-03-10"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if method != "GET" and not self.token:
            raise HeartbeatError("A job-scoped GITHUB_TOKEN is required for the maintenance commit.")
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request("https://api.github.com" + path, data=body, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=20) as response:
                raw = response.read(MAX_API_BYTES + 1)
                if len(raw) > MAX_API_BYTES:
                    raise HeartbeatError("A maintenance API response is oversized.")
                return response.status, json.loads(raw.decode("utf-8"))
        except error.HTTPError as exc:
            # Do not echo API response bodies, request objects, headers or tokens.
            return exc.code, None
        except (error.URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HeartbeatError("The maintenance API request could not complete.") from exc


def maintain(api, repository, branch, now=None, check_only=False):
    if repository.casefold() != REPOSITORY.casefold():
        raise HeartbeatError("Maintenance is restricted to the isolated donor-feed repository.")
    if not isinstance(branch, str) or not 1 <= len(branch) <= 255 or any(ord(c) < 32 for c in branch):
        raise HeartbeatError("A valid default branch is required.")
    now = now or datetime.now(timezone.utc)
    base = "/repos/" + REPOSITORY
    commits = base + "/commits?" + parse.urlencode({"sha": branch, "per_page": 1})
    content = base + "/contents/" + HEARTBEAT_PATH
    for attempt in range(2):
        status, history = api.api("GET", commits)
        if status == 409 or (status == 200 and history == []):
            return {"updated": False, "reason": "repository_has_no_commits"}
        if status != 200 or not isinstance(history, list) or len(history) != 1:
            raise HeartbeatError("Could not read the latest default-branch commit (HTTP " + str(status) + ").")
        try:
            last_commit = utc(history[0]["commit"]["committer"]["date"])
        except (KeyError, TypeError) as exc:
            raise HeartbeatError("The latest repository commit has no valid activity date.") from exc
        status, existing = api.api("GET", content + "?" + parse.urlencode({"ref": branch}))
        previous, sha = None, None
        if status == 200:
            if not isinstance(existing, dict) or existing.get("type") != "file" or existing.get("encoding") != "base64" or not re.fullmatch(r"[a-f0-9]{40,64}", str(existing.get("sha", ""))):
                raise HeartbeatError("The previous heartbeat file metadata is invalid.")
            previous, sha = parse_record(existing.get("content")), existing["sha"]
        elif status != 404:
            raise HeartbeatError("Could not read the previous heartbeat (HTTP " + str(status) + ").")
        if not due(last_commit, previous, now):
            return {"updated": False, "reason": "recent_repository_activity"}
        if check_only:
            return {"updated": False, "reason": "maintenance_commit_due", "checkOnly": True}
        instant = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        record = (json.dumps({"schema": 1, "lastHeartbeatUtc": instant}, indent=2) + "\n").encode("utf-8")
        payload = {"message": "Maintain scheduled donor sync (30-day heartbeat)",
                   "content": base64.b64encode(record).decode("ascii"), "branch": branch,
                   "committer": dict(BOT)}
        if sha is not None:
            payload["sha"] = sha
        status, _result = api.api("PUT", content, payload)
        if status in (200, 201):
            return {"updated": True, "reason": "heartbeat_committed", "path": HEARTBEAT_PATH, "committedUtc": instant}
        if status != 409:
            raise HeartbeatError("The maintenance commit was rejected (HTTP " + str(status) + ").")
        # A concurrent writer changed this file. Re-read both repository activity
        # and its SHA; a fresh heartbeat/commit should now make this run a no-op.
    raise HeartbeatError("A concurrent update prevented maintenance; retry on the next scheduled run.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", REPOSITORY))
    parser.add_argument("--branch", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not args.check_only and not token:
        raise HeartbeatError("Run maintenance with its job-scoped GITHUB_TOKEN, or use --check-only.")
    print(json.dumps(maintain(GitHub(token), args.repository, args.branch, check_only=args.check_only), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (HeartbeatError, OSError) as exc:
        print("Donor sync maintenance refused: " + str(exc), file=sys.stderr)
        sys.exit(1)
