import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import PRACTICE_END, atomic_write, download, evaluate
from .submission import GitHubStore, RemoteError, run_live
from .validation import validate_answer


def main():
    parser = argparse.ArgumentParser(description="DSO429 pipeline. Graded execution requires explicit activation.")
    commands = parser.add_subparsers(dest="command", required=True)
    practice = commands.add_parser("practice", help="Solve and evaluate all 48 practice rounds")
    practice.add_argument("--data-dir", type=Path, default=Path(".cache/quiz"))
    practice.add_argument("--output-dir", type=Path, default=Path("build/practice"))
    practice.add_argument("--offline", action="store_true", help="Use supplied local CSV snapshots")
    practice.add_argument("--scheduled", action="store_true", help="Skip scheduled practice runs after the practice period")
    validate = commands.add_parser("validate", help="Strictly validate an answer file without submitting")
    validate.add_argument("path", type=Path)
    commands.add_parser("live", help="Dormant graded runtime; requires explicit activation and ephemeral runner token")
    args = parser.parse_args()
    try:
        if args.command == "live":
            if os.environ.get("ENABLE_GRADED_SUBMISSIONS") != "true":
                raise ValueError("Graded execution disabled: explicit activation required")
            store = GitHubStore(os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY"))
            questions = download("questions.csv", Path(".cache/quiz"))
            report = run_live(questions, store, authorized=True)
            atomic_write(Path("build/live/status.json"), json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            return 1 if report.get("failed") else 0
        if args.command == "validate":
            validate_answer(args.path.read_text(encoding="utf-8"), args.path.name)
            print("Valid: " + str(args.path))
            return 0
        if args.scheduled and datetime.now(timezone.utc) >= PRACTICE_END:
            print("Practice period ended; scheduled practice run skipped. Graded execution remains disabled.")
            return 0
        if args.offline:
            questions = (args.data_dir / "questions.csv").read_text(encoding="utf-8-sig")
            keys = (args.data_dir / "practice_keys.csv").read_text(encoding="utf-8-sig")
        else:
            questions = download("questions.csv", args.data_dir)
            keys = download("practice_keys.csv", args.data_dir)
        report, directory = evaluate(questions, keys, args.output_dir)
        print(json.dumps({"correct": report["correct"], "total": report["total"],
                          "passed": report["passed"], "report": str(directory / "evaluation.json"),
                          "failed_rounds": report["failures"], "mismatches": report["mismatches"]}, indent=2))
        return 0 if report["passed"] else 1
    except (ValueError, OSError, RemoteError) as exc:
        if args.command == "live":
            atomic_write(Path("build/live/status.json"), json.dumps({"status": "error", "error": str(exc)}) + "\n")
        print("Pipeline failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
