from datetime import date

from app.ai.factguard import allowed
from app.domain.money import Money
from app.domain.nlu import extract
from app.domain.workflow import can


def test_deck_example_intent():
    p = extract("4 days in Kochi from Chennai, under ₹30,000, no early flights", date(2026, 11, 26))
    assert (p.origin, p.destination, p.nights) == ("MAA", "COK", 3)
    assert p.budget == Money.of(30000) and p.constraints == {"depart_after": "08:00"}


def test_indian_grouping():
    assert Money.of(126400).format() == "₹1,26,400" and Money.of(27900).format() == "₹27,900"


def test_workflow_refuses_skips():
    assert can("await_approval", "execute") and not can("search", "execute") and not can("done", "search")


def test_factguard_blocks_invented_numbers():
    draft = "I found 2 plans. Value: ₹27,260."
    assert allowed("Two plans fit; Value is ₹27,260.", draft, "")
    assert not allowed("Value is ₹19,999 and confirmed.", draft, "")
