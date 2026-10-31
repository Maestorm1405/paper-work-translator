from paperwork_translator.checks import check_bill, count_syllables, reading_grade


def line(description, amount, kind="charge", date="09/22/2026"):
    return {"description": description, "date": date, "amount": amount, "kind": kind}


def test_no_bill_means_no_check():
    assert check_bill(None) == (None, [])
    assert check_bill({"lines": [], "total_charges_stated": None, "amount_due_stated": None, "shows_all_lines": True}) == (None, [])


def test_bill_that_adds_up_has_no_concerns():
    bill = {
        "lines": [line("Office visit", 200.10), line("Lab test", 99.95), line("Insurance", 250.05, "insurance_paid")],
        "total_charges_stated": 300.05,
        "amount_due_stated": 50.00,
        "shows_all_lines": True,
    }
    summary, concerns = check_bill(bill)
    assert concerns == []
    assert summary["charges_total"] == 300.05  # no floating-point drift
    assert summary["total_matches"] is True and summary["amount_due_matches"] is True


def test_amount_due_is_not_checked_when_some_lines_are_missing():
    bill = {"lines": [line("Visit", 100)], "total_charges_stated": 100, "amount_due_stated": 40, "shows_all_lines": False}
    summary, concerns = check_bill(bill)
    assert concerns == [] and summary["amount_due_matches"] is None


def test_repeated_charges_need_same_description_date_and_amount():
    bill = {
        "lines": [
            line("Blood  test", 20),
            line("blood test", 20),
            line("Blood test", 20, date="09/23/2026"),
            line("Blood test", 25),
            line("Refund", 20, "you_paid"),
            line("Refund", 20, "you_paid"),
        ],
        "total_charges_stated": None,
        "amount_due_stated": None,
        "shows_all_lines": False,
    }
    summary, concerns = check_bill(bill)
    assert summary["repeated_charges"] == 1
    assert len(concerns) == 1 and "listed 2 times" in concerns[0]["issue"]


def test_syllables():
    assert [count_syllables(w) for w in ["the", "medicine", "take", "table", "hospital", "rhythm"]] == [1, 3, 1, 2, 3, 1]


def test_reading_grade_needs_enough_words_and_ranks_texts():
    assert reading_grade("Too short.") is None
    easy = "You had a cold. Drink lots of water. Rest at home. Take one pill at night. " * 3
    hard = ("Administer the prescribed antimicrobial medication intermittently, maintaining therapeutic concentrations "
            "throughout the duration of treatment, notwithstanding symptomatic improvement. ") * 2
    assert reading_grade(easy) < 4 < reading_grade(hard)
