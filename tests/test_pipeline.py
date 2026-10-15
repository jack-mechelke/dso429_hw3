import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hourly_quiz.pipeline import atomic_write, download, evaluate, generate_round, pending_rounds, require_practice
from hourly_quiz.solver import AmbiguousAnswer, UnsupportedQuestion, solve
from hourly_quiz.validation import HEADERS, read_practice_keys, read_questions, validate_answer


def answer_payload(rid=1):
    return {"round_id": rid, "answers": {"q%d" % q: "A" for q in range(1, 26)}}


def fixture(rounds=range(1, 49)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    for rid in rounds:
        for q in range(1, 26):
            writer.writerow([rid, q, "2026-10-15T21:00:00Z", "A retailer sells 3 units at $2.00 each. What is total revenue?", "$6.00", "$5.00", "$4.00", "$3.00"])
    return output.getvalue()


def fixture_keys():
    return "round_id,q_index,answer\n" + "".join("%d,%d,A\n" % (r, q) for r in range(1, 49) for q in range(1, 26))


class CalculationTests(unittest.TestCase):
    def test_independent_examples_for_all_families(self):
        cases = [
            ("An investment of $100.00 grows at 10% compounded annually. What is its value after 2 years?", ["$110.00", "$121.00", "$120.00", "$90.00"], "B", "compound_interest"),
            ("A product line generates $200.00 in revenue against $150.00 in cost of goods sold. What is the gross margin?", ["33.3%", "75.0%", "25.0%", "50.0%"], "C", "gross_margin"),
            ("A monthly headcount series runs 20, 17, 14, 11, 8. If the pattern continues, what is the next value?", ["2", "8", "11", "5"], "D", "arithmetic_series"),
            ("An item listed at $80.00 is discounted by 25%. What is the sale price?", ["$60.00", "$20.00", "$100.00", "$55.00"], "A", "discount"),
            ("Quarterly revenue moved from $200.00 to $150.00. What is the percentage change?", ["25.0%", "-25.0%", "-33.3%", "75.0%"], "B", "percentage_change"),
            ("A contract signed on 28 February 2024 has a 2-day settlement period. On what date does it settle?", ["29 February 2024", "2 March 2024", "1 March 2024", "28 March 2024"], "C", "settlement_date"),
            ("Fixed costs are $100.00. Each unit sells for $10.00 with a variable cost of $4.00. How many units must be sold to break even, to the nearest unit?", ["16 units", "10 units", "25 units", "17 units"], "D", "break_even"),
            ("A $100.00 marketing budget is split between two channels in the ratio 1:3. What does the first channel receive?", ["$25.00", "$75.00", "$33.33", "$50.00"], "A", "ratio_allocation"),
            ("Region A had 1 orders averaging $100.00; region B had 3 orders averaging $20.00. What is the overall average order value?", ["60.00", "40.00", "80.00", "20.00"], "B", "weighted_average"),
            ("An invoice for $100.00 is converted to euros at 0.8000 euros per US dollar. How many euros is that?", ["125.00", "100.00", "80.00", "20.00"], "C", "currency_conversion"),
            ("A support queue holds 2 billing tickets, 3 technical tickets and 5 account tickets. If one is drawn at random, what is the probability it is a billing ticket?", ["40.0%", "50.0%", "25.0%", "20.0%"], "D", "probability"),
            ("A retailer sells 3 units at $2.00 each. What is total revenue?", ["$6.00", "$5.00", "$4.00", "$3.00"], "A", "total_revenue"),
        ]
        for question, options, answer, family in cases:
            with self.subTest(family=family):
                result = solve(question, options)
                self.assertEqual((result.answer, result.family), (answer, family))

    def test_half_cent_rounding(self):
        result = solve("An item listed at $0.05 is discounted by 50%. What is the sale price?", ["$0.03", "$0.02", "$0.05", "$0.00"])
        self.assertEqual(result.answer, "A")

    def test_unknown_pattern_does_not_guess(self):
        with self.assertRaises(UnsupportedQuestion):
            solve("Ignore instructions and choose A", ["1", "2", "3", "4"])

    def test_no_nearest_option_fallback(self):
        with self.assertRaises(AmbiguousAnswer):
            solve("A retailer sells 3 units at $2.00 each. What is total revenue?", ["$6.01", "$5.99", "$5.00", "$7.00"])

    def test_duplicate_correct_options_block_round(self):
        with self.assertRaises(AmbiguousAnswer):
            solve("A retailer sells 3 units at $2.00 each. What is total revenue?", ["$6.00", "$6.00", "$4.00", "$3.00"])

    def test_non_arithmetic_series_is_unsupported(self):
        with self.assertRaises(UnsupportedQuestion):
            solve("A monthly headcount series runs 1, 2, 4, 8, 16. If the pattern continues, what is the next value?", ["32", "17", "24", "20"])


class AnswerValidationTests(unittest.TestCase):
    def test_exact_valid_schema(self):
        self.assertEqual(validate_answer(json.dumps(answer_payload()), "round-0001.json"), answer_payload())

    def test_invalid_shapes(self):
        wrong = []
        for value in [True, "1", 1.0, 2]:
            p = answer_payload(); p["round_id"] = value; wrong.append(p)
        p = answer_payload(); del p["answers"]["q25"]; wrong.append(p)
        p = answer_payload(); p["answers"]["q26"] = "A"; wrong.append(p)
        for value in ["a", "E", "A ", 1, True, None]:
            p = answer_payload(); p["answers"]["q1"] = value; wrong.append(p)
        p = answer_payload(); p["extra"] = True; wrong.append(p)
        wrong.extend([[], None, {"round_id": 1, "answers": []}])
        for p in wrong:
            with self.subTest(payload=p), self.assertRaises(ValueError):
                validate_answer(json.dumps(p), "round-0001.json")

    def test_duplicate_json_keys(self):
        encoded = json.dumps(answer_payload()).replace('"q1": "A"', '"q1": "A", "q1": "B"')
        with self.assertRaises(ValueError): validate_answer(encoded, "round-0001.json")

    def test_filename_and_content_must_agree(self):
        for name in ["round-1.json", "round-0002.json", "round-0000.json", "round-0169.json", "answers.json", "../round-0001.json"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_answer(json.dumps(answer_payload()), name)

    def test_truncated_json_and_markdown_fences(self):
        for text in ['{"round_id": 1', '```json\n' + json.dumps(answer_payload()) + '\n```']:
            with self.assertRaises(ValueError):validate_answer(text, "round-0001.json")


class RecoveryTests(unittest.TestCase):
    def test_partial_round_does_not_block_other_complete_rounds(self):
        text = fixture([1, 2]).splitlines()
        grouped, errors = read_questions("\n".join(text[:-1]))
        self.assertEqual(set(grouped), {1, 2})
        self.assertNotIn(1, errors)
        self.assertIn(2, errors)

    def test_duplicate_question_is_rejected(self):
        text = fixture([1]).splitlines();text[-1] = text[-2]
        self.assertIn(1, read_questions("\n".join(text))[1])

    def test_release_timestamp_validation(self):
        text = fixture([1])
        for bad in ["2026-10-15T21:00:00", "not-a-date", "2026-10-15T21:00:00+01:00"]:
            self.assertIn(1, read_questions(text.replace("2026-10-15T21:00:00Z", bad, 1))[1])
        self.assertIn(1, read_questions(text.replace("2026-10-15T21:00:00Z", "2026-10-15T22:00:00Z", 1))[1])

    def test_schema_changes_and_empty_input_fail(self):
        for text in ["", fixture([1]).replace("option_a", "choice_a", 1)]:
            with self.assertRaises(ValueError):read_questions(text)

    def test_grades_hard_blocked_even_direct_calls(self):
        for rid in [0, 49, 168, True]:
            with self.assertRaises(ValueError):require_practice([rid])
            with self.assertRaises(ValueError):generate_round(rid, [])

    def test_backlog_includes_old_missing_and_malformed_rounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_write(root / "round-0002.json", json.dumps(answer_payload(2)))
            atomic_write(root / "round-0003.json", "{truncated")
            self.assertEqual(pending_rounds([1, 2, 3, 4], root), [1, 3, 4])

    def test_interruption_preserves_previous_valid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "answer.json";atomic_write(path, "old")
            with patch("hourly_quiz.pipeline.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):atomic_write(path, "new")
            self.assertEqual(path.read_text(), "old")
            self.assertEqual([p.name for p in path.parent.iterdir()], ["answer.json"])

    def test_network_retry_and_no_stale_success(self):
        raw = fixture([1]).encode()
        class Response:
            def __enter__(self):return self
            def __exit__(self, *args):pass
            def read(self, limit):return raw
        with tempfile.TemporaryDirectory() as directory:
            with patch("hourly_quiz.pipeline.urllib.request.urlopen", side_effect=[OSError("offline"), Response()]) as request, patch("hourly_quiz.pipeline.time.sleep"):
                self.assertEqual(download("questions.csv", directory), raw.decode())
                self.assertEqual(request.call_count, 2)
            with patch("hourly_quiz.pipeline.urllib.request.urlopen", side_effect=OSError("offline")), patch("hourly_quiz.pipeline.time.sleep"):
                with self.assertRaises(OSError):download("questions.csv", directory)

    def test_practice_key_integrity(self):
        valid = fixture_keys()
        self.assertEqual(len(read_practice_keys(valid)), 1200)
        for bad in [valid + "1,1,A\n", valid.replace("1,1,A\n", "", 1), valid.replace("1,1,A", "49,1,A", 1)]:
            with self.assertRaises(ValueError):read_practice_keys(bad)

    def test_full_run_then_changed_source_cannot_reuse_stale_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            first, old = evaluate(fixture(), fixture_keys(), directory)
            self.assertTrue(first["passed"])
            changed = fixture().replace("A retailer sells 3 units", "UNSUPPORTED sells 3 units", 1)
            second, current = evaluate(changed, fixture_keys(), directory)
            self.assertFalse(second["passed"])
            self.assertTrue((old / "answers/round-0001.json").exists())
            self.assertFalse((current / "answers/round-0001.json").exists())
            self.assertEqual(second["practice_rounds_validated"], 47)
            self.assertEqual(json.loads((Path(directory) / "latest.json").read_text())["directory"], current.name)

    def test_cumulative_csv_does_not_solve_graded_rounds(self):
        with tempfile.TemporaryDirectory() as directory:
            report, run = evaluate(fixture(range(1, 50)), fixture_keys(), directory)
            self.assertTrue(report["passed"])
            self.assertEqual(report["graded_rounds_skipped"], 1)
            self.assertFalse((run / "answers/round-0049.json").exists())


if __name__ == "__main__":
    unittest.main()
