"""Practice-only pipeline. Practice keys enter evaluation after solving, never the solver."""

import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .solver import solve
from .validation import read_practice_keys, read_questions, validate_answer


SOURCE = "https://raw.githubusercontent.com/FaisalXL/dso429-hourly-quiz/main/"
MAX_PRACTICE_ROUND = 48
PRACTICE_END = datetime(2026, 10, 17, 21, tzinfo=timezone.utc)


def require_practice(round_ids):
    if any(type(r) is not int or not 1 <= r <= MAX_PRACTICE_ROUND for r in round_ids):
        raise ValueError("Graded execution is disabled. Only practice rounds 1–48 are allowed.")


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def download(filename, data_dir, attempts=3):
    if filename not in {"questions.csv", "practice_keys.csv"}:
        raise ValueError("Unapproved source filename")
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(SOURCE + filename, headers={"User-Agent": "dso429-practice-pipeline", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(10_000_001)
            if len(raw) > 10_000_000:
                raise ValueError("Source exceeded 10 MB limit")
            text = raw.decode("utf-8-sig")
            # Validate schema before replacing the last snapshot.
            (read_questions if filename == "questions.csv" else read_practice_keys)(text)
            atomic_write(Path(data_dir) / filename, text)
            return text
        except (OSError, urllib.error.URLError):
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def pending_rounds(round_ids, output_dir):
    """Reconcile ALL rounds against validated local files, never only the latest."""
    require_practice(round_ids)
    pending = []
    for rid in sorted(round_ids):
        path = Path(output_dir) / ("round-%04d.json" % rid)
        try:
            validate_answer(path.read_text(encoding="utf-8"), path.name, rid)
        except (OSError, ValueError):
            pending.append(rid)
    return pending


def generate_round(rid, rows, allow_graded=False):
    if not allow_graded:
        require_practice([rid])
    elif type(rid) is not int or not 1 <= rid <= 168:
        raise ValueError("Round ID outside assignment range")
    if len(rows) != 25 or {int(row["q_index"]) for row in rows} != set(range(1, 26)):
        raise ValueError("Incomplete or duplicate round questions")
    answers, solutions = {}, {}
    for row in sorted(rows, key=lambda r: int(r["q_index"])):
        if int(row["round_id"]) != rid:
            raise ValueError("Question belongs to a different round")
        q = int(row["q_index"])
        try:
            solution = solve(row["question_text"], [row["option_" + letter] for letter in "abcd"])
        except (ValueError, ArithmeticError, KeyError) as exc:
            raise ValueError("q%d: %s" % (q, exc)) from exc
        answers["q%d" % q] = solution.answer
        solutions[q] = solution
    encoded = json.dumps({"round_id": rid, "answers": answers}, indent=2) + "\n"
    validate_answer(encoded, "round-%04d.json" % rid, rid)
    return encoded, solutions


def evaluate(questions_text, keys_text, output_root):
    """Rebuild into a new snapshot, so stale answers cannot hide a current failure."""
    started = time.monotonic()
    grouped, input_errors = read_questions(questions_text)
    selected = sorted(r for r in grouped if r <= MAX_PRACTICE_ROUND)
    require_practice(selected)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=output_root, prefix="run-"))
    answer_dir = stage / "answers"
    answer_dir.mkdir()
    predictions, failures = {}, {}
    for rid in range(1, 49):
        if rid not in grouped or rid in input_errors:
            failures[str(rid)] = input_errors.get(rid, "Practice round missing from source")
            continue
        try:
            encoded, solutions = generate_round(rid, grouped[rid])
            atomic_write(answer_dir / ("round-%04d.json" % rid), encoded)
            predictions[rid] = solutions
        except ValueError as exc:
            failures[str(rid)] = str(exc)

    # Keys are parsed only after every prediction has been calculated.
    keys = read_practice_keys(keys_text)
    correct = Counter()
    totals = Counter()
    family_totals, family_correct = Counter(), Counter()
    mismatches, rounds = [], []
    for rid in range(1, 49):
        round_correct = 0
        split = "development" if rid <= 32 else "held_out"
        for q in range(1, 26):
            totals[split] += 1
            solution = predictions.get(rid, {}).get(q)
            if solution:
                family_totals[solution.family] += 1
                if solution.answer == keys[rid, q]:
                    correct[split] += 1
                    family_correct[solution.family] += 1
                    round_correct += 1
                else:
                    mismatches.append({"round_id": rid, "q_index": q, "predicted": solution.answer,
                                       "expected": keys[rid, q], "family": solution.family, "calculated": solution.calculated})
        rounds.append({"round_id": rid, "correct": round_correct, "total": 25,
                       "validated": rid in predictions, "error": failures.get(str(rid))})
    report = {
        "mode": "practice_only", "graded_execution_enabled": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "questions_sha256": hashlib.sha256(questions_text.encode()).hexdigest(),
        "keys_sha256": hashlib.sha256(keys_text.encode()).hexdigest(),
        "source_rounds_seen": len(grouped), "practice_rounds_validated": len(predictions),
        "graded_rounds_skipped": len([r for r in grouped if r > 48]),
        "correct": sum(correct.values()), "total": 1200,
        "accuracy": sum(correct.values()) / 1200,
        "splits": {s: {"correct": correct[s], "total": totals[s]} for s in ["development", "held_out"]},
        "split_method": "Templates inspected without keys; rounds 33–48 keys held out until final evaluation. Re-running this report is regression testing, not a new holdout.",
        "families": {f: {"correct": family_correct[f], "total": family_totals[f]} for f in sorted(family_totals)},
        "failures": failures, "mismatches": mismatches, "rounds": rounds,
        "elapsed_seconds": round(time.monotonic() - started, 4),
        "passed": len(predictions) == 48 and not failures and not mismatches,
    }
    atomic_write(stage / "evaluation.json", json.dumps(report, indent=2) + "\n")
    summary = ("# Practice validation\n\n"
               "- Correct: {correct}/{total} ({accuracy:.2%})\n"
               "- Validated rounds: {practice_rounds_validated}/48\n"
               "- Development: {dev}/800; held-out: {hold}/400\n"
               "- Failed rounds: {failed}; mismatched questions: {mismatched}\n"
               "- Graded execution: **disabled**\n"
               "- Runtime: {elapsed_seconds}s\n\n"
               "This is practice evaluation, not a submission or a guarantee about unseen graded questions.\n").format(
                   **report, dev=correct["development"], hold=correct["held_out"],
                   failed=len(failures), mismatched=len(mismatches))
    atomic_write(stage / "summary.md", summary)
    # The marker selects a complete immutable run directory. Interrupted earlier
    # runs are never treated as current; retaining the previous run preserves evidence.
    atomic_write(output_root / "latest.json", json.dumps({"directory": stage.name, "passed": report["passed"]}) + "\n")
    atomic_write(output_root / "summary.md", summary)
    return report, stage
