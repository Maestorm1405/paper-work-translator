"""Reads a paper on this computer, with no AI and no key: explains abbreviations and medical words, finds medicines,
dates, warning signs, and phone numbers, and copies bill lines so the app's own math check can run on them.

It is simpler than the AI translation, so the result says so and points people back to the original paper."""

from __future__ import annotations

import datetime as dt
import io
import re
from typing import Any

# Abbreviations written out in plain words. Order matters: longer patterns first.
ABBREVIATIONS = [
    (r"\bq(\d+)\s*-\s*(\d+)\s*h(rs?|ours?)?\b", r"every \1 to \2 hours"),
    (r"\bq(\d+)\s*h(rs?|ours?)?\b", r"every \1 hours"),
    (r"\bx\s*(\d+)\s*(days?|d)\b", r"for \1 days"),
    (r"\bw/o\b", "without"),
    (r"\bw/", "with "),
    (r"\bPO\b", "by mouth"),
    (r"\bBID\b", "2 times a day"),
    (r"\bTID\b", "3 times a day"),
    (r"\bQID\b", "4 times a day"),
    (r"\b(QD|daily)\b", "once a day"),
    (r"\bQHS\b", "at bedtime"),
    (r"\bPRN\b", "only when needed for"),
    (r"\bNPO\b", "nothing to eat or drink"),
    (r"\btabs?\b", "tablets"),
    (r"\bcaps?\b", "capsules"),
    (r"\bhrs?\b", "hours"),
    (r"\bL/day\b", "liters a day"),
    (r"\b(ED|ER)\b", "emergency room"),
    (r"\b(?:[Yy]our |the )?PCP\b", "your regular doctor"),
    (r"\bHx\b", "Health history"),
    (r"\bDx\b", "Diagnosis"),
    (r"\bRx\b", "prescription"),
    (r"\bf/u\b", "follow-up"),
    (r"\bpt\b", "patient"),
    (r">\s*(?=\d)", "over "),
    (r"<\s*(?=\d)", "under "),
]

GLOSSARY = {
    "UTI": "an infection in the parts of your body that make and store pee",
    "cystitis": "an infection of the bladder, where pee is stored",
    "hematuria": "blood in your pee",
    "flank": "the side of your body, between your ribs and hip",
    "spasticity": "stiff or tight muscles that are hard to control",
    "catheter": "a thin tube put into the body, for example to drain pee",
    "neurogenic bladder": "a bladder that doesn't work well because of a nerve problem",
    "discharge": "leaving the hospital to go home",
    "follow-up": "a visit after this one, to check how you are doing",
    "contrast": "a dye that makes a scan picture clearer",
    "CT": "a scan that takes detailed pictures of the inside of your body",
    "MRI": "a scan that uses magnets to take pictures of the inside of your body",
    "metabolic panel": "a blood test that checks your body's chemistry, like sugar and salts",
    "CBC": "a blood test that counts your blood cells",
    "infusion": "medicine or fluid given slowly through a tube into a vein",
    "IV": "a small tube in a vein, used to give medicine or fluid",
    "CPT": "a code number for each service, used for billing",
    "HCPCS": "a code number for equipment or services, used for billing",
    "contractual adjustment": "an amount the hospital takes off because of its deal with your insurance",
    "guarantor": "the person who is responsible for paying the bill",
    "deductible": "the amount you pay each year before insurance starts to pay",
    "copay": "a set amount you pay for a visit or medicine",
    "coinsurance": "the part of the cost you pay, as a percent, after the deductible",
    "premium": "what you pay each month to have insurance",
    "claim": "a request to your insurance to pay for care",
    "explanation of benefits": "a letter from insurance showing what they paid. It is not a bill",
    "EOB": "a letter from insurance showing what they paid. It is not a bill",
    "in-network": "doctors and hospitals that have a deal with your insurance, so they cost less",
    "out-of-network": "doctors and hospitals without a deal with your insurance, so they can cost more",
    "prior authorization": "getting OK from insurance before you get the care",
    "medical necessity": "proof that you really need the care for your health",
    "appeal": "asking the insurance company to change its decision",
    "adverse benefit determination": "a decision by insurance not to pay for something",
    "DME": "medical equipment you use at home, like a wheelchair",
    "expedited": "fast",
    "physician": "a doctor",
    "diagnosis": "what the doctor thinks is wrong",
    "benign": "not cancer",
    "malignant": "cancer",
    "biopsy": "taking a tiny piece of the body to test it",
    "hypertension": "high blood pressure",
    "A1C": "a blood test that shows your average blood sugar over 3 months",
    "ECG": "a test that checks your heartbeat",
    "EKG": "a test that checks your heartbeat",
    "referral": "your doctor sending you to another doctor",
    "MRN": "your medical record number at this hospital",
    "DOB": "date of birth",
    "collections": "a company that tries to collect unpaid bills. It can hurt your credit",
    "financial assistance": "help from the hospital to pay less or nothing",
}

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
DATE_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
DATE_WORDS = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
TIME = re.compile(r"\b(\d{1,2}):(\d{2})\s*([AP])\.?M\.?\b", re.I)
MONEY = re.compile(r"(-?)\s*\$\s?(\d[\d,]*\.\d{2})")
PHONE = re.compile(r"(?:1-)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
DOSE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|g|mL|ml|units?)\b")
WARNING = re.compile(r"\b(911|emergency|return to the (ed|er)|go to the (ed|er|hospital)|call your (doctor|pcp) (for|if)|"
                     r"seek (medical|immediate)|right away)\b", re.I)
TODO_WORDS = re.compile(r"\b(due|by|appointment|follow|schedule|visit|appeal|deadline|return|bring|clinic|"
                        r"urology|see|pay|submit|send)\b", re.I)
NOT_A_TODO = re.compile(r"\b(dob|birth|statement date|admit|discharge date|date of service|service date)\b", re.I)
KEY_WORDS = re.compile(r"\b(must|do not|don't|never|complete|continue|stop|avoid|take|bring|important|will be|"
                       r"denied|not approved|not covered|right to|may be available|increase|use|call)\b", re.I)


def pdf_text(data: bytes) -> str:
    """The words in a PDF that has real text (not a scan)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:20])


def plain(line: str) -> str:
    """One line of the paper with abbreviations written out and extra spaces removed."""
    text = " ".join(line.replace("•", " ").split()).lstrip("-*0123456789.) ").strip()
    for pattern, replacement in ABBREVIATIONS:
        # Upper-case abbreviations only match as written ("PO", not "po").
        text = re.sub(pattern, replacement, text, flags=0 if re.search(r"[A-Z]{2}", pattern) else re.I)
    return text[:1].upper() + text[1:] if text else text


def _date(line: str) -> dt.date | None:
    for match in DATE_NUMERIC.finditer(line):
        month, day, year = map(int, match.groups())
        try:
            return dt.date(year, month, day)
        except ValueError:
            continue
    for match in DATE_WORDS.finditer(line):
        try:
            return dt.date(int(match.group(3)), MONTHS[match.group(1).lower()], int(match.group(2)))
        except ValueError:
            continue
    return None


def _time(line: str) -> str | None:
    match = TIME.search(line)
    if not match:
        return None
    hour, minute, half = int(match.group(1)) % 12, match.group(2), match.group(3).upper()
    return f"{hour + (12 if half == 'P' else 0):02d}:{minute}"


def document_type(text: str) -> str:
    lower = text.lower()
    if re.search(r"statement|amount due|balance due|total charges|you owe", lower):
        return "A medical bill"
    if re.search(r"explanation of benefits|benefit determination|denied|appeal|prior authorization|claim", lower):
        return "A letter from your insurance"
    if re.search(r"discharge", lower):
        return "Instructions for going home from the hospital"
    if re.search(r"result|reference range|lab|specimen", lower):
        return "Test results"
    if re.search(r"prescription|pharmacy|refill|\brx\b", lower):
        return "A prescription or medicine list"
    return "A medical paper"


def _bill(lines: list[str]) -> dict[str, Any] | None:
    bill_lines, total, due = [], None, None
    for raw in lines:
        match = None
        for match in MONEY.finditer(raw):
            pass  # use the last amount on the line
        if not match:
            continue
        amount = float(match.group(2).replace(",", ""))
        negative = bool(match.group(1))
        lower = raw.lower()
        if re.search(r"total", lower):
            total = amount
            continue
        if re.search(r"amount due|balance due|you owe|patient responsibility|please pay", lower):
            due = amount
            continue
        date = DATE_NUMERIC.search(raw)
        description = MONEY.sub("", DATE_NUMERIC.sub("", raw)).strip(" -:\t") or "Charge"
        if re.search(r"insurance|plan paid|plan payment", lower):
            kind = "insurance_paid"
        elif re.search(r"patient payment|you paid|payment.*thank|your payment", lower):
            kind = "you_paid"
        elif negative or re.search(r"adjust|discount|write.?off|credit", lower):
            kind = "discount_or_adjustment"
        else:
            kind = "charge"
        bill_lines.append({"description": " ".join(description.split()), "date": date.group(0) if date else None,
                           "amount": amount, "kind": kind})
    if not any(line["kind"] == "charge" for line in bill_lines):
        return None
    return {"lines": bill_lines, "total_charges_stated": total, "amount_due_stated": due,
            "shows_all_lines": total is not None and due is not None}


def _concerns(text: str, lines: list[str], medicines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    concerns = []
    # The same medicine with different numbers of days.
    for medicine in medicines:
        name = medicine["name"].split()[0].lower()
        days = {int(d) for line in lines if name in line.lower()
                for d in re.findall(r"\b(\d+)[- ]?(?:days?\b|d\b)|x\s*(\d+)\s*d", line, re.I) for d in d if d}
        if len(days) > 1:
            listed = " and ".join(f"{d} days" for d in sorted(days))
            concerns.append({
                "severity": "high",
                "issue": f"The paper gives different lengths of time for {medicine['name'].split()[0]}: {listed}.",
                "why_it_matters": "Taking a medicine for the wrong number of days can mean it doesn't work.",
                "what_to_do": "Ask your doctor or pharmacist how many days to take it.",
            })
    # More than one date of birth.
    births = {m.group(0) for line in lines if re.search(r"\b(dob|birth)\b", line, re.I)
              for m in [DATE_NUMERIC.search(line) or DATE_WORDS.search(line)] if m}
    if len(births) > 1:
        concerns.append({
            "severity": "high",
            "issue": f"This paper shows more than one date of birth: {', '.join(sorted(births))}.",
            "why_it_matters": "A wrong date of birth can cause a claim to be denied or records to be mixed up.",
            "what_to_do": "Call the office that sent this paper and ask them to fix it.",
        })
    if re.search(r"\bappeal\b", text, re.I) and re.search(r"\b(within|by)\b", text, re.I):
        concerns.append({
            "severity": "medium",
            "issue": "This paper has a deadline to appeal.",
            "why_it_matters": "If you miss the deadline, you may lose the chance to change the decision.",
            "what_to_do": "Write down the deadline. Ask your doctor's office or someone you trust to help you appeal.",
        })
    if re.search(r"\bcollections?\b", text, re.I):
        concerns.append({
            "severity": "medium",
            "issue": "This paper says unpaid bills may be sent to collections.",
            "why_it_matters": "That can hurt your credit.",
            "what_to_do": "If you can't pay by the due date, call the billing office. Ask about a payment plan or "
                          "financial help.",
        })
    return concerns


QUESTIONS = {
    "A medical bill": ["Can you send me an itemized bill?", "Did my insurance pay everything it should?",
                       "Can I get financial help or a payment plan?"],
    "A letter from your insurance": ["Why was this not approved?", "How do I appeal, and what papers do you need?",
                                     "Can my doctor help with the appeal?"],
    "Instructions for going home from the hospital": ["Which medicines should I keep taking, and for how long?",
                                                      "When should I see my doctor again?",
                                                      "Which signs mean I should get help right away?"],
}


def explain(text: str, reading_level: str = "simple") -> dict[str, Any]:
    """A Translation-shaped result made without AI."""
    lines = [line for line in text.splitlines() if line.strip()]
    kind = document_type(text)
    used: set[int] = set()

    warning_signs = []
    for i, line in enumerate(lines):
        if WARNING.search(line):
            warning_signs.append(plain(line))
            used.add(i)

    medicines = []
    for i, line in enumerate(lines):
        if i in used or MONEY.search(line) or not DOSE.search(line):
            continue
        clean = " ".join(line.split()).lstrip("-*0123456789.) ")
        dose = DOSE.search(clean)
        name = clean[:dose.end()].strip()
        rest = plain(clean[dose.end():]).strip(" .,:") or "Take as the paper says."
        if re.match(r"(do not|don't)", name, re.I):
            continue
        medicines.append({"name": name, "how_to_take": rest[:1].upper() + rest[1:] + ".", "notes": None})
        used.add(i)

    todos = []
    for i, line in enumerate(lines):
        if i in used or NOT_A_TODO.search(line):
            continue
        date = _date(line)
        relative = re.search(r"\bin (\d+|one|two|three) (days?|weeks?|months?)\b", line, re.I)
        if (date or relative) and TODO_WORDS.search(line) and not (MONEY.search(line) and DATE_NUMERIC.match(line.strip())):
            todos.append({"task": plain(line), "when": (f"{date:%B} {date.day}, {date.year}" if date else relative.group(0))
                          + (f" at {TIME.search(line).group(0)}" if TIME.search(line) else ""),
                          "date": date.isoformat() if date else None, "time": _time(line)})
            used.add(i)

    bill = _bill(lines) if kind == "A medical bill" else None
    if bill:
        used.update(i for i, line in enumerate(lines) if MONEY.search(line))

    key_points = []
    for i, line in enumerate(lines):
        if i in used or len(line.split()) < 4:
            continue
        if KEY_WORDS.search(line) or PHONE.search(line) or line.lstrip().startswith(("-", "•")):
            key_points.append(plain(line))
    limit = 6 if reading_level == "very_simple" else 10
    key_points = key_points[:limit]

    lower = text.lower()
    words = [{"term": term, "meaning": meaning} for term, meaning in GLOSSARY.items()
             if re.search(r"\b" + re.escape(term.lower()) + r"\b", lower)]

    summary = [f"This looks like {kind[0].lower() + kind[1:]}."]
    if bill and bill["amount_due_stated"] is not None:
        summary.append(f"It says you owe ${bill['amount_due_stated']:,.2f}.")
    if re.search(r"\b(denied|not approved|not covered)\b", lower):
        summary.append("It says something was not approved. You may be able to appeal.")
    if kind == "Instructions for going home from the hospital":
        summary.append("It tells you how to take care of yourself at home.")

    unclear = ["This was read on your computer without AI. Some parts may be missing or not explained. "
               "Always check the original paper."]
    if len(text.split()) < 25:
        unclear.append("We found very few words. If this was a photo, try a clearer one with more light.")

    return {
        "is_medical_document": True,
        "document_type": kind,
        "summary": " ".join(summary),
        "warning_signs": warning_signs,
        "concerns": _concerns(text, lines, medicines),
        "key_points": key_points,
        "todos": todos,
        "medicines": medicines,
        "words_to_know": words,
        "questions_for_doctor": QUESTIONS.get(kind, ["What does this paper mean for me?",
                                                     "Is there anything I need to do?"]),
        "unclear_parts": unclear,
        "bill": bill,
        "original_text": text,
    }
