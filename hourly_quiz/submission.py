"""Dormant graded runtime; tests use synthetic data and an in-memory remote.

No live graded operation is authorized by installing this module. The CLI and
cloud workflow additionally require ENABLE_GRADED_SUBMISSIONS=true explicitly.
"""

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .pipeline import generate_round
from .validation import read_questions, utc_timestamp, validate_answer


REPOSITORY = "jack-mechelke/dso429_hw3"
GRADED_START = datetime(2026, 10, 17, 21, tzinfo=timezone.utc)
# Conservative handout-cover cutoff. The final-deadline discrepancy remains
# unresolved; do not silently extend this to the later Brightspace cutoff.
GRADED_STOP = datetime(2026, 10, 22, 21, tzinfo=timezone.utc)
# All practice rows share one upstream release time. This deliberately labeled
# simulation exposes one more real practice round each hour for end-to-end tests.
PRACTICE_SIM_START = datetime(2026, 10, 15, 20, tzinfo=timezone.utc)
PRACTICE_STOP = GRADED_START


class RemoteError(RuntimeError):
    pass


class RetryableConflict(RemoteError):
    pass


@dataclass
class Snapshot:
    head: str
    tree: str
    files: dict


class GitHubStore:
    """Use the runner's short-lived token, never a token in a URL or git config."""

    def __init__(self, token, repository):
        if repository != REPOSITORY or not token:
            raise ValueError("Expected the approved repository and an ephemeral runner token")
        self.token = token

    def request(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            "https://api.github.com/repos/" + REPOSITORY + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "User-Agent": "dso429-hourly-pipeline"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.load(response)
                server_date = response.headers.get("Date")
                return body, server_date
        except urllib.error.HTTPError as exc:
            # Never include headers, tokens, or the untrusted response body in logs.
            if exc.code in {409, 422} and method == "PATCH":
                raise RetryableConflict("main changed during submission; reconciling") from None
            raise RemoteError("GitHub %s request failed (HTTP %d)" % (method, exc.code)) from None
        except (OSError, ValueError) as exc:
            # A timeout on a ref update may have succeeded. Re-read main rather
            # than blindly repeating a mutation against a stale head.
            raise RetryableConflict("GitHub response unavailable; reconcile remote state before retry") from None

    def snapshot(self):
        metadata, _ = self.request("GET", "")
        if metadata.get("private") is not False or metadata.get("default_branch") != "main":
            raise RemoteError("Submission repository must remain public with main as default branch")
        ref, _ = self.request("GET", "/git/ref/heads/main")
        head = ref["object"]["sha"]
        commit, _ = self.request("GET", "/git/commits/" + head)
        tree_sha = commit["tree"]["sha"]
        tree, _ = self.request("GET", "/git/trees/" + tree_sha + "?recursive=1")
        if tree.get("truncated"):
            raise RemoteError("Remote tree was truncated; cannot reconcile safely")
        files = {}
        for item in tree["tree"]:
            path = item["path"]
            if not path.startswith("answers/round-") or not path.endswith(".json"):
                continue
            if item["type"] != "blob" or item.get("size", 0) > 20000:
                raise RemoteError("Unexpected answer entry on main")
            blob, _ = self.request("GET", "/git/blobs/" + item["sha"])
            if blob.get("encoding") != "base64":
                raise RemoteError("Unexpected blob encoding")
            try:
                files[path] = base64.b64decode(blob["content"], validate=False).decode("utf-8")
            except (ValueError, UnicodeError):
                files[path] = ""  # Malformed existing file remains pending.
        return Snapshot(head, tree_sha, files)

    def publish(self, snapshot, files):
        for path, text in files.items():
            if not path.startswith("answers/") or path.count("/") != 1:
                raise ValueError("Only required answer files may be published")
            validate_answer(text, path.split("/")[1])
        tree, _ = self.request("POST", "/git/trees", {
            "base_tree": snapshot.tree,
            "tree": [{"path": path, "mode": "100644", "type": "blob", "content": text}
                     for path, text in sorted(files.items())]})
        commit, _ = self.request("POST", "/git/commits", {
            "message": "Submit validated quiz rounds " + ", ".join(path[14:18] for path in sorted(files)),
            "tree": tree["sha"], "parents": [snapshot.head]})
        _, server_date = self.request("PATCH", "/git/refs/heads/main", {"sha": commit["sha"], "force": False})
        return {"commit_sha": commit["sha"], "github_response_date": server_date}


def run_live(questions, store, now=None, authorized=False, attempts=3):
    return _run_submission(questions, store, now, authorized, attempts, mode="graded")


def run_practice_submission(questions, store, now=None, authorized=False, attempts=3):
    return _run_submission(questions, store, now, authorized, attempts, mode="practice_simulation")


def _run_submission(questions, store, now, authorized, attempts, mode):
    if not authorized:
        raise ValueError("Submission execution is disabled; explicit activation is required")
    practice = mode == "practice_simulation"
    first, last = (1, 48) if practice else (49, 168)
    start, stop = (PRACTICE_SIM_START, PRACTICE_STOP) if practice else (GRADED_START, GRADED_STOP)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("UTC-aware runtime clock required")
    if not start <= now < stop:
        return {"mode": mode, "status": "outside_execution_window", "submitted": [], "failed": {}}
    grouped, errors = read_questions(questions)
    target = sorted(r for r in grouped if first <= r <= last)
    base = {"mode": mode, "checked_at_utc": now.isoformat(), "submitted": [], "already_present": [],
            "failed": {}, "future_rounds_skipped": [], "rounds_seen": target, "receipts": [],
            "source_released_at_utc": {}, "eligible_at_utc": {},
            "release_policy": "simulated: October 15 20:00 UTC + (round_id - 1) hours" if practice else "actual source released_at",
            "receipt_note": "Remote read-back proves contents exist on main. GitHub HTTP Date bounds response time; commit timestamps are never treated as push receipt time. Official timing is determined by the instructor's GitHub push records."}
    last_conflict = None
    acknowledged = {}
    for attempt in range(attempts):
        try:
            snapshot = store.snapshot()
            files = {}
            for rid in target:
                key = str(rid)
                if rid in errors:
                    base["failed"][key] = errors[rid]
                    continue
                rows = grouped[rid]
                released = utc_timestamp(rows[0]["released_at"])
                eligible = PRACTICE_SIM_START + timedelta(hours=rid - 1) if practice else released
                base["source_released_at_utc"][key] = released.isoformat()
                base["eligible_at_utc"][key] = eligible.isoformat()
                if eligible > now:
                    if rid not in base["future_rounds_skipped"]:base["future_rounds_skipped"].append(rid)
                    continue
                path = "answers/round-%04d.json" % rid
                if path in snapshot.files:
                    try:
                        validate_answer(snapshot.files[path], path.split("/")[1], rid)
                        # Never rewrite a valid existing submission. This also
                        # respects the first-late-version lock after three hours.
                        if rid not in base["already_present"]:base["already_present"].append(rid)
                        continue
                    except ValueError:
                        pass
                try:
                    encoded, _ = generate_round(rid, rows, allow_graded=not practice)
                    files[path] = encoded
                except ValueError as exc:
                    base["failed"][key] = str(exc)
            if not files:
                base["status"] = "complete" if not base["failed"] else "needs_attention"
                base["submitted"] = sorted(acknowledged)
                return base
            # Recheck the actual clock before mutation in real runs. Synthetic
            # tests supply now explicitly via their fake store clock.
            clock = getattr(store, "clock", lambda: datetime.now(timezone.utc))()
            if not start <= clock < stop:
                raise RemoteError("Submission window closed before publish")
            receipt = store.publish(snapshot, files)
            verified = store.snapshot()
            confirmed_at = getattr(store, "clock", lambda: datetime.now(timezone.utc))()
            batch = []
            for path, encoded in files.items():
                if verified.files.get(path) != encoded:
                    raise RetryableConflict("Remote read-back differs; reconcile before retry")
                rid = int(path[14:18])
                acknowledged[rid] = True
                batch.append(rid)
            receipt.update({"rounds": sorted(batch), "verified_at_utc": confirmed_at.isoformat()})
            base["receipts"].append(receipt)
            base["submitted"] = sorted(acknowledged)
            base["status"] = "complete" if not base["failed"] else "needs_attention"
            return base
        except RetryableConflict as exc:
            last_conflict = str(exc)
            continue
    raise RemoteError("Remote state could not be reconciled after %d attempts: %s" % (attempts, last_conflict))
