"""Checks the app does itself instead of trusting the AI: bill math, repeated charges, and reading level."""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from typing import Any

CENT = Decimal("0.01")


def _money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


def _fmt(value: Decimal) -> str:
    return f"${value:,.2f}"


def check_bill(bill: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Return a summary of the bill math plus concerns for totals that don't add up or repeated charges."""
    if not bill or not bill.get("lines"):
        return None, []

    lines = bill["lines"]
    charges = [line for line in lines if line["kind"] == "charge"]
    credits = [line for line in lines if line["kind"] != "charge"]
    charges_total = sum((_money(line["amount"]) for line in charges), Decimal("0.00"))
    credits_total = sum((_money(line["amount"]) for line in credits), Decimal("0.00"))
    concerns: list[dict[str, Any]] = []

    stated_total = bill.get("total_charges_stated")
    total_matches = None
    if stated_total is not None and charges:
        stated_total = _money(stated_total)
        total_matches = stated_total == charges_total
        if not total_matches:
            concerns.append(
                {
                    "severity": "high",
                    "issue": f"The charges on this bill add up to {_fmt(charges_total)}, "
                    f"but the bill says the total is {_fmt(stated_total)}.",
                    "why_it_matters": f"That is a difference of {_fmt(abs(stated_total - charges_total))}. "
                    "The bill may have a math mistake.",
                    "what_to_do": "Before you pay, call the billing office and ask them to check the total.",
                }
            )

    stated_due = bill.get("amount_due_stated")
    computed_due = None
    due_matches = None
    if stated_due is not None and bill.get("shows_all_lines") and charges:
        stated_due = _money(stated_due)
        computed_due = charges_total - credits_total
        due_matches = stated_due == computed_due
        if not due_matches:
            concerns.append(
                {
                    "severity": "high",
                    "issue": f"Using the charges and payments listed, you would owe {_fmt(computed_due)}, "
                    f"but the bill says you owe {_fmt(stated_due)}.",
                    "why_it_matters": f"You might be asked to pay {_fmt(abs(stated_due - computed_due))} "
                    f"{'more' if stated_due > computed_due else 'less'} than the lines add up to.",
                    "what_to_do": "Ask the billing office to explain how they got the amount you owe.",
                }
            )

    def key(line: dict[str, Any]) -> tuple[str, str, Decimal]:
        return (" ".join(line["description"].lower().split()), (line.get("date") or "").strip(), _money(line["amount"]))

    counts = Counter(key(line) for line in charges)
    duplicates = []
    for line in charges:
        k = key(line)
        if counts[k] > 1:
            duplicates.append({"description": line["description"], "date": k[1] or None, "amount": float(k[2]), "times": counts[k]})
            counts[k] = 0  # report each repeated charge once
    for dup in duplicates:
        when = f" on {dup['date']}" if dup["date"] else ""
        concerns.append(
            {
                "severity": "medium",
                "issue": f"\"{dup['description']}\"{when} is listed {dup['times']} times, "
                f"for {_fmt(_money(dup['amount']))} each.",
                "why_it_matters": "You might be charged more than once for the same thing. "
                "Sometimes this is correct, for example if you really had the test twice.",
                "what_to_do": "Ask the billing office if you really got this more than once. "
                "You can ask for an itemized bill that lists every charge.",
            }
        )

    summary = {
        "charges_total": float(charges_total),
        "total_charges_stated": float(stated_total) if stated_total is not None else None,
        "total_matches": total_matches,
        "amount_due_stated": float(stated_due) if stated_due is not None else None,
        "amount_due_computed": float(computed_due) if computed_due is not None else None,
        "amount_due_matches": due_matches,
        "repeated_charges": len(duplicates),
    }
    return summary, concerns


# ---------- Reading level (Flesch-Kincaid grade, English only) ----------

_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def count_syllables(word: str) -> int:
    w = word.lower()
    if len(w) <= 3:
        return 1
    if w.endswith("e") and not w.endswith(("le", "ee")):
        w = w[:-1]
    return max(1, len(re.findall(r"[aeiouy]+", w)))


def reading_grade(text: str, min_words: int = 30) -> float | None:
    """Approximate U.S. school grade needed to read the text. None if there isn't enough English text."""
    words = _WORD.findall(text or "")
    sentences = [s for s in re.split(r"[.!?]+|\n+", text or "") if _WORD.search(s)]
    if len(words) < min_words or not sentences:
        return None
    syllables = sum(count_syllables(w) for w in words)
    grade = 0.39 * len(words) / len(sentences) + 11.8 * syllables / len(words) - 15.59
    return round(max(grade, 1.0), 1)


def plain_text(translation: dict[str, Any]) -> str:
    """The plain-language version as running text, one sentence per line, for scoring."""
    items = [translation.get("summary", "")]
    items += translation.get("key_points", [])
    items += translation.get("warning_signs", [])
    items += [t["task"] for t in translation.get("todos", [])]
    items += [m["how_to_take"] for m in translation.get("medicines", [])]
    items += translation.get("questions_for_doctor", [])
    return "\n".join(i.strip() for i in items if i and i.strip())
