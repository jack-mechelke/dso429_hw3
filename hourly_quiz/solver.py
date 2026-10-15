"""Pure calculations. No network, answer keys, model calls, or filesystem access."""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext


class UnsupportedQuestion(ValueError):
    pass


class AmbiguousAnswer(ValueError):
    pass


@dataclass(frozen=True)
class Solution:
    answer: str
    family: str
    calculated: str


N = r"(-?\d[\d,]*(?:\.\d+)?)"
MONTHS = {name: i for i, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}


def number(value):
    return Decimal(value.replace(",", ""))


def match_numeric(value, options, family, places):
    """Require one exact option at the question's stated display precision."""
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    matches = []
    for letter, option in zip("ABCD", options):
        cleaned = option.strip().removeprefix("$").removesuffix(" units").removesuffix("%")
        if re.fullmatch(r"-?\d[\d,]*(?:\.\d+)?", cleaned) is None:
            raise AmbiguousAnswer("Non-numeric option in a numeric question")
        if number(cleaned) == rounded:
            matches.append(letter)
    if len(matches) != 1:
        raise AmbiguousAnswer("Calculated result must match exactly one option; found %d" % len(matches))
    return Solution(matches[0], family, str(rounded))


def solve(text, options):
    if len(options) != 4 or any(not isinstance(o, str) or not o.strip() for o in options):
        raise AmbiguousAnswer("Exactly four nonempty options required")
    with localcontext() as context:
        context.prec = 40
        return _solve(text, options)


def _solve(text, options):
    def match(pattern):
        found = re.fullmatch(pattern, text)
        return tuple(number(v) for v in found.groups()) if found else None

    v = match(r"An investment of \$" + N + r" grows at " + N + r"% compounded annually\. What is its value after " + N + r" years\?")
    if v:
        principal, rate, years = v
        if years != int(years) or not 0 <= years <= 1000:
            raise UnsupportedQuestion("Unsupported compounding duration")
        return match_numeric(principal * (1 + rate / 100) ** int(years), options, "compound_interest", 2)
    v = match(r"A product line generates \$" + N + r" in revenue against \$" + N + r" in cost of goods sold\. What is the gross margin\?")
    if v:
        revenue, cost = v
        return match_numeric((revenue - cost) / revenue * 100, options, "gross_margin", 1)
    m = re.fullmatch(r"A monthly headcount series runs (.+)\. If the pattern continues, what is the next value\?", text)
    if m:
        values = [number(s.strip()) for s in m[1].split(",")]
        if len(values) < 3 or len(set(b - a for a, b in zip(values, values[1:]))) != 1:
            raise UnsupportedQuestion("Headcount series is not an arithmetic progression")
        return match_numeric(values[-1] + values[-1] - values[-2], options, "arithmetic_series", 0)
    v = match(r"An item listed at \$" + N + r" is discounted by " + N + r"%\. What is the sale price\?")
    if v:
        price, discount = v
        return match_numeric(price * (1 - discount / 100), options, "discount", 2)
    v = match(r"Quarterly revenue moved from \$" + N + r" to \$" + N + r"\. What is the percentage change\?")
    if v:
        old, new = v
        return match_numeric((new - old) / old * 100, options, "percentage_change", 1)
    m = re.fullmatch(r"A contract signed on (\d{1,2}) ([A-Za-z]+) (\d{4}) has a (\d+)-day settlement period\. On what date does it settle\?", text)
    if m:
        day, month, year, days = m.groups()
        settlement = date(int(year), MONTHS[month], int(day)) + timedelta(days=int(days))
        result = "%d %s %d" % (settlement.day, next(k for k, v in MONTHS.items() if v == settlement.month), settlement.year)
        matches = [letter for letter, option in zip("ABCD", options) if option == result]
        if len(matches) != 1:
            raise AmbiguousAnswer("Settlement date must match exactly one option")
        return Solution(matches[0], "settlement_date", result)
    v = match(r"Fixed costs are \$" + N + r"\. Each unit sells for \$" + N + r" with a variable cost of \$" + N + r"\. How many units must be sold to break even, to the nearest unit\?")
    if v:
        fixed, price, variable = v
        if price <= variable:
            raise UnsupportedQuestion("Nonpositive contribution margin")
        # The question explicitly asks for nearest unit, rather than ceiling.
        return match_numeric(fixed / (price - variable), options, "break_even", 0)
    v = match(r"A \$" + N + r" marketing budget is split between two channels in the ratio " + N + r":" + N + r"\. What does the first channel receive\?")
    if v:
        budget, first, second = v
        return match_numeric(budget * first / (first + second), options, "ratio_allocation", 2)
    v = match(r"Region A had " + N + r" orders averaging \$" + N + r"; region B had " + N + r" orders averaging \$" + N + r"\. What is the overall average order value\?")
    if v:
        count_a, average_a, count_b, average_b = v
        return match_numeric((count_a * average_a + count_b * average_b) / (count_a + count_b), options, "weighted_average", 2)
    v = match(r"An invoice for \$" + N + r" is converted to euros at " + N + r" euros per US dollar\. How many euros is that\?")
    if v:
        dollars, rate = v
        return match_numeric(dollars * rate, options, "currency_conversion", 2)
    v = match(r"A support queue holds " + N + r" billing tickets, " + N + r" technical tickets and " + N + r" account tickets\. If one is drawn at random, what is the probability it is a billing ticket\?")
    if v:
        billing, technical, account = v
        return match_numeric(billing / (billing + technical + account) * 100, options, "probability", 1)
    v = match(r"A retailer sells " + N + r" units at \$" + N + r" each\. What is total revenue\?")
    if v:
        quantity, price = v
        return match_numeric(quantity * price, options, "total_revenue", 2)
    raise UnsupportedQuestion("Unrecognized question pattern; manual review required")
