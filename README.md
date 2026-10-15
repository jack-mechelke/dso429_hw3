# DSO 429 Homework 3 — The Hourly Quiz

This repository contains a deterministic, practice-only quiz pipeline. It uses Python's standard library, requires no model API key, and can run on GitHub-hosted runners while the student's computer is off.

## Run locally

Python 3.9 or newer (3.11+ recommended):

```sh
python3 -m unittest discover -s tests -v
python3 -m hourly_quiz practice
```

The practice command downloads the instructor's question CSV and practice keys over HTTPS, calculates answers independently, validates all 48 rounds, then compares predictions against the keys. Keys are never passed to the solver. Reports separate development rounds 1–32 from rounds 33–48, whose keys were initially held out. Later executions are regression checks.

For an existing local snapshot:

```sh
python3 -m hourly_quiz practice --offline --data-dir /path/to/snapshots
python3 -m hourly_quiz validate build/practice/run-EXAMPLE/answers/round-0001.json
```

Generated files live under ignored `build/practice/run-*/`. `latest.json` selects the latest completed evaluation; `summary.md` provides the result. CSV snapshots in `.cache/quiz/` are ignored. No command in this version commits or pushes answers.

## Cloud practice checks

The **Practice validation** workflow runs after pipeline changes reach `main`, on manual **Actions → Practice validation → Run workflow**, and at UTC minutes 07, 22, 37, and 52 during October 15–17. Scheduled practice evaluation stops at October 17, 2026, 21:00 UTC (2 PM Pacific). A manual run remains available afterward for practice regression only. The calendar cron is annual, but the fixed year/end guard prevents solving on later scheduled runs; remove or replace the practice schedule at the next phase.

Each cloud run performs the tests, evaluates practice data, writes an Actions summary, and retains evaluation artifacts for seven days. Its token has only `contents: read`, checkout does not persist credentials, and it cannot push answer files. No personal access token or stored credential is needed. Workflow failure notifications depend on the repository owner's GitHub notification settings; an independent watchdog is not installed.

GitHub documents that scheduled jobs may be delayed or dropped under load: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule . Multiple checks per hour reduce reliance on one invocation but are not an availability guarantee. For graded operation, assess measured scheduling behavior and whether a separate always-on worker or independent watchdog is warranted.

## Safety and correctness

- Covers 12 observed arithmetic/date families with Decimal calculations and calendar-day date arithmetic. Unknown wording or nonunique choices fail that round rather than guess.
- Requires all 25 unique questions with consistent explicit UTC release times. Partial or malformed rounds fail without preventing other complete rounds from being evaluated.
- Exact `round_id`/filename agreement and q1–q25 uppercase answers are enforced, including duplicate JSON key rejection. See `answer.schema.json`; the Python validator additionally checks filenames and duplicate keys.
- Every run recalculates all practice rounds into a new directory, so stale files cannot hide failures. Reconciliation considers every missing or invalid round, and atomic writes prevent partial files from appearing complete.
- Graded round IDs are rejected by the generator even if called directly. The command ignores graded questions present in the cumulative CSV. It never generates a graded answer file.
- Do not publish private course instructions, credentials, session logs, or transcripts. The `.gitignore` allows only reviewed source, tests, workflow, schema, README, and required answer paths.

## Before graded operation

Graded solving/submission is not enabled. A separately authorized next phase must implement and test remote submission receipt verification, idempotent reconciliation with `main`, recovery after failed pushes/restarts, late-answer locking, an execution cutoff consistent with clarified final deadlines, and independent liveness alerts. Local file existence or commit timestamps are not proof GitHub received answers. Known practice performance does not guarantee coverage of unseen graded wording.

When authorized, submission files must be `answers/round-NNNN.json` on `main`. Keep this individual repository public and its owner/name unchanged until grades are released. Register its URL in Brightspace by October 16, 2026, 11:59 PM Pacific. Final deadline ambiguity remains: cover October 22 at 2 PM versus Brightspace cutoff 11:59 PM; the last release is 1 PM. The student reported on October 15 that the instructor removed the usage-report requirement; no Claude tracker is installed or used by this pipeline.
