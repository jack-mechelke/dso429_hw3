import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import PRACTICE_END, download, evaluate
from .validation import validate_answer


def main():
    parser = argparse.ArgumentParser(description="Practice-only DSO429 pipeline; no commits or pushes")
    commands = parser.add_subparsers(dest="command", required=True)
    practice = commands.add_parser("practice", help="Solve and evaluate all 48 practice rounds")
    practice.add_argument("--data-dir", type=Path, default=Path(".cache/quiz"))
    practice.add_argument("--output-dir", type=Path, default=Path("build/practice"))
    practice.add_argument("--offline", action="store_true", help="Use supplied local CSV snapshots")
    practice.add_argument("--scheduled", action="store_true", help="Skip scheduled practice runs after the practice period")
    validate = commands.add_parser("validate", help="Strictly validate an answer file without submitting")
    validate.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
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
    except (ValueError, OSError) as exc:
        print("Pipeline failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
