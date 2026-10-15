"""Submission tests use synthetic arithmetic and a fake remote; no real graded data."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from hourly_quiz.pipeline import generate_round
from hourly_quiz.submission import GRADED_STOP, PRACTICE_SIM_START, PRACTICE_STOP, GitHubStore, RemoteError, RetryableConflict, Snapshot, run_live, run_practice_submission
from hourly_quiz.__main__ import main
from test_pipeline import answer_payload, fixture


NOW = datetime(2026, 10, 17, 22, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self):
        self.files = {}
        self.head = "initial"
        self.published = []
        self.conflict = False
        self.timeout_after_success = False
        self.clock = lambda: NOW

    def snapshot(self):
        return Snapshot(self.head, "tree", dict(self.files))

    def publish(self, snapshot, files):
        if self.conflict:
            self.conflict = False
            self.files["README.md"] = "another writer's update"
            self.head = "concurrent-update"
            raise RetryableConflict("concurrent writer")
        if snapshot.head != self.head:
            raise RetryableConflict("stale head")
        self.files.update(files)
        self.head = "commit-%d" % len(self.files)
        self.published.append(dict(files))
        if self.timeout_after_success:
            self.timeout_after_success = False
            raise RetryableConflict("response lost after successful push")
        return {"commit_sha": self.head, "github_response_date": "Sat, 17 Oct 2026 22:00:00 GMT"}


class SubmissionTests(unittest.TestCase):
    def run_fake(self, text=None, store=None, now=NOW):
        store = store or FakeStore()
        return run_live(text or fixture([49, 50]), store, now=now, authorized=True), store

    def test_activation_required_before_any_access(self):
        with self.assertRaises(ValueError):run_live(fixture([49]), None, now=NOW)
        with patch.dict("os.environ", {}, clear=True), patch("sys.argv", ["hourly_quiz", "live"]), patch("hourly_quiz.__main__.download") as fetch:
            self.assertEqual(main(), 1)
            fetch.assert_not_called()

    def test_bound_to_approved_repository(self):
        for token, repo in [("", "jack-mechelke/dso429_hw3"), ("token", "someone/else")]:
            with self.assertRaises(ValueError):GitHubStore(token, repo)

    def test_window_gates_prevent_any_remote_access(self):
        for now in [datetime(2026, 10, 17, 20, 59, tzinfo=timezone.utc), GRADED_STOP]:
            report = run_live(fixture([49]), None, now=now, authorized=True)
            self.assertEqual(report["status"], "outside_execution_window")

    def test_all_missed_rounds_share_one_verified_publish(self):
        report, store = self.run_fake()
        self.assertEqual(report["submitted"], [49, 50])
        self.assertEqual(len(store.published), 1)
        self.assertEqual(report["receipts"][0]["rounds"], [49, 50])
        self.assertIn("verified_at_utc", report["receipts"][0])

    def test_restart_does_not_resubmit_valid_existing_answers(self):
        _, store = self.run_fake()
        report, _ = self.run_fake(store=store)
        self.assertEqual(report["submitted"], [])
        self.assertEqual(report["already_present"], [49, 50])
        self.assertEqual(len(store.published), 1)

    def test_existing_late_file_is_never_rewritten(self):
        store = FakeStore()
        existing = json.dumps(answer_payload(49))
        store.files["answers/round-0049.json"] = existing
        report, _ = self.run_fake(store=store, now=datetime(2026, 10, 18, tzinfo=timezone.utc))
        self.assertEqual(store.files["answers/round-0049.json"], existing)
        self.assertEqual(report["submitted"], [50])

    def test_invalid_existing_file_is_recovered(self):
        store = FakeStore();store.files["answers/round-0049.json"] = "{truncated"
        report, _ = self.run_fake(store=store)
        self.assertEqual(report["submitted"], [49, 50])

    def test_partial_new_round_does_not_block_older_round(self):
        text = "\n".join(fixture([49, 50]).splitlines()[:-1])
        report, _ = self.run_fake(text=text)
        self.assertEqual(report["submitted"], [49])
        self.assertIn("50", report["failed"])

    def test_unknown_question_blocks_only_its_round(self):
        text = fixture([49, 50]).replace("A retailer sells", "Unknown template sells", 1)
        report, _ = self.run_fake(text=text)
        self.assertEqual(report["submitted"], [50])
        self.assertIn("49", report["failed"])

    def test_future_release_is_never_submitted(self):
        text = fixture([49]).replace("2026-10-15T21:00:00Z", "2026-10-18T21:00:00Z")
        report, store = self.run_fake(text=text)
        self.assertEqual(report["future_rounds_skipped"], [49])
        self.assertEqual(store.published, [])

    def test_optimistic_conflict_preserves_other_writer(self):
        store = FakeStore();store.conflict = True
        report, _ = self.run_fake(store=store)
        self.assertEqual(report["submitted"], [49, 50])
        self.assertEqual(store.files["README.md"], "another writer's update")
        self.assertEqual(len(store.published), 1)

    def test_uncertain_response_is_reconciled_without_duplicate_push(self):
        store = FakeStore();store.timeout_after_success = True
        report, _ = self.run_fake(store=store)
        self.assertEqual(len(store.published), 1)
        self.assertEqual(report["already_present"], [49, 50])

    def test_persistently_failed_readback_is_not_reported_as_success(self):
        store = FakeStore()
        def publish(snapshot, files):
            return {"commit_sha": "not-actually-visible", "github_response_date": None}
        store.publish = publish
        with self.assertRaises(RemoteError):self.run_fake(store=store)

    def test_crossing_final_cutoff_prevents_publish(self):
        store = FakeStore();store.clock = lambda: GRADED_STOP
        with self.assertRaises(RemoteError):self.run_fake(store=store)
        self.assertEqual(store.published, [])

    def test_publish_preserves_base_tree_and_refuses_force(self):
        store = GitHubStore("synthetic-test-token", "jack-mechelke/dso429_hw3")
        responses = [({"sha": "new-tree"}, None), ({"sha": "new-commit"}, None), ({}, "Sat, 17 Oct 2026 22:00:00 GMT")]
        encoded = json.dumps(answer_payload(49))
        with patch.object(store, "request", side_effect=responses) as request:
            store.publish(Snapshot("old-head", "old-tree", {}), {"answers/round-0049.json": encoded})
            self.assertEqual(request.call_args_list[0].args[2]["base_tree"], "old-tree")
            self.assertEqual(request.call_args_list[1].args[2]["parents"], ["old-head"])
            self.assertIs(request.call_args_list[2].args[2]["force"], False)

    def test_publisher_refuses_unrelated_paths(self):
        store = GitHubStore("synthetic-test-token", "jack-mechelke/dso429_hw3")
        with self.assertRaises(ValueError):store.publish(Snapshot("head", "tree", {}), {"private.txt": "secret"})


class HourlyPracticeTests(unittest.TestCase):
    def run_at(self, now, store=None, rounds=range(1, 50)):
        store = store or FakeStore()
        store.clock = lambda: now
        report = run_practice_submission(fixture(rounds), store, now=now, authorized=True)
        return report, store

    def test_first_run_publishes_only_first_eligible_practice_round(self):
        report, store = self.run_at(PRACTICE_SIM_START + timedelta(minutes=30))
        self.assertEqual(report["mode"], "practice_simulation")
        self.assertEqual(report["submitted"], [1])
        self.assertEqual(set(store.files), {"answers/round-0001.json"})
        self.assertNotEqual(report["eligible_at_utc"]["1"], report["source_released_at_utc"]["1"])

    def test_next_hour_discovers_next_round_and_skips_previous(self):
        _, store = self.run_at(PRACTICE_SIM_START + timedelta(minutes=30))
        report, _ = self.run_at(PRACTICE_SIM_START + timedelta(hours=1), store)
        self.assertEqual(report["submitted"], [2])
        self.assertEqual(report["already_present"], [1])
        self.assertEqual(len(store.published), 2)

    def test_repeat_in_same_hour_creates_no_duplicate_commit(self):
        now = PRACTICE_SIM_START + timedelta(minutes=30)
        _, store = self.run_at(now)
        report, _ = self.run_at(now + timedelta(minutes=1), store)
        self.assertEqual(report["submitted"], [])
        self.assertEqual(len(store.published), 1)

    def test_delayed_start_catches_all_eligible_backlog(self):
        report, store = self.run_at(PRACTICE_SIM_START + timedelta(hours=4))
        self.assertEqual(report["submitted"], [1, 2, 3, 4, 5])
        self.assertEqual(len(store.published), 1)

    def test_never_selects_or_solves_a_graded_round(self):
        report, store = self.run_at(PRACTICE_STOP - timedelta(seconds=1))
        self.assertEqual(report["submitted"], list(range(1, 49)))
        self.assertNotIn(49, report["rounds_seen"])
        self.assertFalse(any(int(path[14:18]) > 48 for path in store.files))

    def test_practice_window_stops_before_graded_phase(self):
        report = run_practice_submission(fixture(), None, now=PRACTICE_STOP, authorized=True)
        self.assertEqual(report["status"], "outside_execution_window")

    def test_practice_submission_requires_authorized_call(self):
        with self.assertRaises(ValueError):
            run_practice_submission(fixture(), None, now=PRACTICE_SIM_START)


if __name__ == "__main__":unittest.main()
