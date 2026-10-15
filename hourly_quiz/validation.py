"""Reject malformed inputs and outputs before producing any submission file."""

import csv
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timezone


HEADERS = ["round_id", "q_index", "released_at", "question_text", "option_a", "option_b", "option_c", "option_d"]


def utc_timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("released_at must include an explicit UTC timezone")
    return parsed.astimezone(timezone.utc)


def integer(value, low, high):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9]\d*", value):
        raise ValueError("Invalid integer field")
    result = int(value)
    if not low <= result <= high:
        raise ValueError("Integer field outside assignment range")
    return result


def read_questions(text):
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != HEADERS:
        raise ValueError("Question CSV schema changed")
    grouped, errors = defaultdict(list), {}
    for line, row in enumerate(reader, 2):
        # Invalid round identity cannot safely be assigned to a recoverable group.
        rid = integer(row.get("round_id"), 1, 168)
        try:
            if None in row or any(row.get(k) is None or not row[k].strip() for k in HEADERS):
                raise ValueError("CSV row has missing or extra fields")
            integer(row["q_index"], 1, 25)
            utc_timestamp(row["released_at"])
        except (ValueError, TypeError) as exc:
            errors[rid] = "Invalid CSV row %d: %s" % (line, exc)
        grouped[rid].append(row)
    for rid, rows in grouped.items():
        if rid in errors:
            continue
        if len(rows) != 25 or {int(r["q_index"]) for r in rows} != set(range(1, 26)):
            errors[rid] = "Round must have exactly 25 unique questions numbered 1–25"
        elif len({utc_timestamp(r["released_at"]) for r in rows}) != 1:
            errors[rid] = "Inconsistent release timestamps within round"
    if not grouped:
        raise ValueError("Empty questions CSV")
    return dict(grouped), errors


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def validate_answer(text, filename, expected_round=None):
    match = re.fullmatch(r"round-(\d{4})\.json", filename)
    if not match:
        raise ValueError("Expected round-NNNN.json filename")
    rid = int(match[1])
    if not 1 <= rid <= 168 or (expected_round is not None and rid != expected_round):
        raise ValueError("Filename round mismatch")
    payload = json.loads(text, object_pairs_hook=no_duplicate_keys)
    if not isinstance(payload, dict) or set(payload) != {"round_id", "answers"}:
        raise ValueError("Expected exactly round_id and answers")
    if type(payload["round_id"]) is not int or payload["round_id"] != rid:
        raise ValueError("round_id must be an integer matching the filename")
    answers = payload["answers"]
    if not isinstance(answers, dict) or set(answers) != {"q%d" % q for q in range(1, 26)}:
        raise ValueError("Expected exactly q1 through q25")
    if any(type(v) is not str or v not in {"A", "B", "C", "D"} for v in answers.values()):
        raise ValueError("Answers must be uppercase A, B, C, or D")
    return payload


def read_practice_keys(text):
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != ["round_id", "q_index", "answer"]:
        raise ValueError("Practice key CSV schema changed")
    result = {}
    for row in reader:
        if None in row or any(row.get(k) is None for k in reader.fieldnames):
            raise ValueError("Malformed practice key CSV row")
        identity = (integer(row["round_id"], 1, 48), integer(row["q_index"], 1, 25))
        if identity in result or row["answer"] not in {"A", "B", "C", "D"}:
            raise ValueError("Duplicate or invalid practice answer key")
        result[identity] = row["answer"]
    if set(result) != {(r, q) for r in range(1, 49) for q in range(1, 26)}:
        raise ValueError("Practice keys must cover exactly rounds 1–48")
    return result
