# DSO 429 Homework 3 — The Hourly Quiz

This repository contains a deterministic quiz pipeline with cloud practice validation and a disabled graded-submission runtime. It uses Python's standard library, requires no model API key, and can run on GitHub-hosted runners while the student's computer is off.

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

Generated practice files live under ignored `build/practice/run-*/`. `latest.json` selects the latest completed evaluation; `summary.md` provides the result. CSV snapshots in `.cache/quiz/` are ignored. The practice command never commits or pushes answers.

## Cloud practice checks

The **Practice validation** workflow runs after pipeline changes reach `main`, on manual **Actions → Practice validation → Run workflow**, and at UTC minutes 07, 22, 37, and 52 during October 15–17. Scheduled practice evaluation stops at October 17, 2026, 21:00 UTC (2 PM Pacific). A manual run remains available afterward for practice regression only. The calendar cron is annual, but the fixed year/end guard prevents solving on later scheduled runs; remove or replace the practice schedule at the next phase.

Each cloud run performs the tests, evaluates practice data, writes an Actions summary, and retains evaluation artifacts for seven days. Its token has only `contents: read`, checkout does not persist credentials, and it cannot push answer files. No personal access token or stored credential is needed. Workflow failure notifications depend on the repository owner's GitHub notification settings; an independent watchdog is not installed.

GitHub documents that scheduled jobs may be delayed or dropped under load: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule . Multiple checks per hour reduce reliance on one invocation but are not an availability guarantee. For graded operation, assess measured scheduling behavior and whether a separate always-on worker or independent watchdog is warranted.

## Hourly end-to-end practice submission test

The separate **Hourly practice submission test** workflow really writes practice answer JSON files to `answers/` on `main`, then reads them back to verify the contents. It runs immediately when this workflow is installed, at minute 00 each hour, and through **Actions → Hourly practice submission test → Run workflow**. GitHub may start a scheduled run late; minute 00 is a requested start time, not a guarantee.

All 48 practice rounds were published ahead of time and share the same upstream release timestamp. To test hourly discovery, this workflow uses an explicitly simulated eligibility schedule: round 1 at October 15, 2026, 20:00 UTC (1 PM Pacific), round 2 at 21:00 UTC (2 PM Pacific), and one additional practice round per hour thereafter. Round 48 becomes eligible October 17 at 19:00 UTC (noon Pacific). The first run after installation handles currently eligible rounds. A delayed or missed run catches up all eligible unanswered rounds. The source timestamps are preserved separately in the report; they are not rewritten or represented as hourly source releases.

This command downloads questions only, calculates independently of practice keys, enforces rounds 1–48, preserves valid existing answers, and stops at October 17 at 2 PM Pacific. It uses the same remote-publication and read-back machinery as the dormant graded runtime. Its temporary runner token has `contents: write` only for this job; no personal token or persistent credential is created. Graded workflow remains hard-disabled.

For each run, inspect the green/red job result, its summary, and the `practice-submission-*` artifact containing `status.json`. A successful publication includes a commit SHA, GitHub response date and verification time. Verify the corresponding `answers/round-NNNN.json` on `main`. Re-running should report the same round as already present and create no duplicate submission. Unknown questions or failed remote verification produce a failed workflow. The original 15-minute **Practice validation** workflow still evaluates all 48 rounds without publishing.

## Safety and correctness

- Covers 12 observed arithmetic/date families with Decimal calculations and calendar-day date arithmetic. Unknown wording or nonunique choices fail that round rather than guess.
- Requires all 25 unique questions with consistent explicit UTC release times. Partial or malformed rounds fail without preventing other complete rounds from being evaluated.
- Exact `round_id`/filename agreement and q1–q25 uppercase answers are enforced, including duplicate JSON key rejection. See `answer.schema.json`; the Python validator additionally checks filenames and duplicate keys.
- Every run recalculates all practice rounds into a new directory, so stale files cannot hide failures. Reconciliation considers every missing or invalid round, and atomic writes prevent partial files from appearing complete.
- Graded round IDs are rejected by the generator by default, even on direct calls. The practice command ignores graded questions present in the cumulative CSV. Only the separately gated live runtime can opt into generating graded files; this capability was tested with synthetic questions, never actual graded questions.
- Do not publish private course instructions, credentials, session logs, or transcripts. The `.gitignore` allows only reviewed source, tests, workflow, schema, README, and required answer paths.

## Graded runtime — built, but disabled

`python3 -m hourly_quiz live` refuses to run unless `ENABLE_GRADED_SUBMISSIONS=true`, the approved `GITHUB_REPOSITORY` is supplied, and a short-lived runner `GITHUB_TOKEN` is available. The graded cloud workflow has a literal false job guard: no repository variable can enable it accidentally. Activation needs a separately authorized workflow edit as well as the explicit environment setting. This setup has not enabled either graded gate or solved any actual graded round. The separately enabled hourly practice test may publish practice rounds 1–48 only.

The dormant runtime fetches all released rounds 49–168, skips future releases, validates every existing remote answer, computes all missing/invalid rounds, and publishes complete validated files together. It preserves all other files using the current Git tree. It never overwrites a valid existing answer, including a locked late submission. Ref updates are fast-forward only. A concurrent update or uncertain push response triggers a fresh reconciliation; a network timeout does not cause blind resubmission. Remote read-back must match every generated byte before the run reports success. Failures in one round do not prevent other complete rounds being submitted.

The report retains the published commit SHA, GitHub HTTP response date and remote verification time. These are evidence that contents reached `main`, not a replacement for the instructor's official push receipt records. Commit author timestamps are never used to claim timing credit. Reports are saved as Actions artifacts; no token is included. The live runtime never downloads practice keys.

The default operating window is October 17 at 2 PM through October 22 at 2 PM Pacific. The final cutoff deliberately follows the earlier handout-cover deadline while its conflict with the later Brightspace cutoff remains unresolved. It checks the clock again before mutation and refuses to submit outside the window. Do not extend it without an explicit decision based on instructor clarification.

Before activation, review measured scheduled-run behavior, confirm GitHub failure notifications, and decide whether to add an independent external watchdog or always-on worker. An independent liveness alert is not installed; the same scheduler cannot reliably detect its own failure. Known practice performance does not guarantee coverage of unseen graded wording. A live answer publication has not been performed as a test; submission and concurrency tests use an in-memory remote with synthetic questions.

When authorized, submission files must be `answers/round-NNNN.json` on `main`. Keep this individual repository public and its owner/name unchanged until grades are released. Register its URL in Brightspace by October 16, 2026, 11:59 PM Pacific. Final deadline ambiguity remains: cover October 22 at 2 PM versus Brightspace cutoff 11:59 PM; the last release is 1 PM. The student reported on October 15 that the instructor removed the usage-report requirement; no Claude tracker is installed or used by this pipeline.
