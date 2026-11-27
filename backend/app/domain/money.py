from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}

# Indicative rates used ONLY to compare a foreign-currency budget against INR quotes.
# Simulated values; never used to price or charge anything.
INDICATIVE_FX_TO_INR = {"INR": 1.0, "USD": 84.5, "EUR": 91.0, "GBP": 106.0, "AED": 23.0, "SGD": 63.0, "THB": 2.45}


class CurrencyMismatch(ValueError):
    pass


def _group_indian(n: int) -> str:
    s = str(abs(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


class Money(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount_minor: int
    currency: str = "INR"

    @classmethod
    def of(cls, major: float, currency: str = "INR") -> Money:
        return cls(amount_minor=int(round(major * 100)), currency=currency)

    @classmethod
    def zero(cls, currency: str = "INR") -> Money:
        return cls(amount_minor=0, currency=currency)

    def _same(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(amount_minor=self.amount_minor + other.amount_minor, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(amount_minor=self.amount_minor - other.amount_minor, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(amount_minor=-self.amount_minor, currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor <= other.amount_minor

    @property
    def major(self) -> float:
        return self.amount_minor / 100

    def to_inr_indicative(self) -> Money:
        rate = INDICATIVE_FX_TO_INR.get(self.currency)
        if rate is None:
            raise CurrencyMismatch(f"no indicative rate for {self.currency}")
        return Money.of(round(self.major * rate), "INR")

    def format(self) -> str:
        whole = round(self.major)
        sign = "-" if whole < 0 else ""
        if self.currency == "INR":
            return f"{sign}₹{_group_indian(whole)}"
        sym = SYMBOLS.get(self.currency)
        body = f"{abs(whole):,}"
        return f"{sign}{sym}{body}" if sym else f"{sign}{self.currency} {body}"


def money_sum(items: Iterable[Money], currency: str = "INR") -> Money:
    total = Money.zero(currency)
    for m in items:
        total = total + m
    return total
