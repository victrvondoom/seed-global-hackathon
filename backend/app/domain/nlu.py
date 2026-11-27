"""Deterministic intent extraction. Used directly when no model is configured, and as the
validator/backstop for model output. Unknown stays unknown: nothing is guessed."""
from __future__ import annotations

import re
from datetime import date, time, timedelta

from app.domain.intent import IntentPatch, Travellers
from app.domain.money import Money

ALIASES: dict[str, str] = {
    "chennai": "MAA", "madras": "MAA", "maa": "MAA",
    "kochi": "COK", "cochin": "COK", "ernakulam": "COK", "fort kochi": "COK", "alleppey": "COK",
    "alappuzha": "COK", "kerala": "COK", "cok": "COK",
    "bengaluru": "BLR", "bangalore": "BLR", "blr": "BLR",
    "mumbai": "BOM", "bombay": "BOM", "bom": "BOM",
    "delhi": "DEL", "new delhi": "DEL", "del": "DEL",
    "hyderabad": "HYD", "hyd": "HYD", "kolkata": "CCU", "calcutta": "CCU", "ccu": "CCU",
    "ahmedabad": "AMD", "pune": "PNQ",
    "goa": "GOI", "panaji": "GOI", "panjim": "GOI",
    "jaipur": "JAI", "udaipur": "UDR",
    "varanasi": "VNS", "banaras": "VNS", "benares": "VNS", "kashi": "VNS",
    "leh": "IXL", "ladakh": "IXL",
    "andaman": "IXZ", "andamans": "IXZ", "port blair": "IXZ", "havelock": "IXZ",
    "dubai": "DXB", "singapore": "SIN", "bangkok": "BKK", "thailand": "BKK",
}
_ALIAS_ORDER = sorted(ALIASES, key=len, reverse=True)

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "a": 1, "an": 1}
NUM = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

TAG_WORDS = {
    "heritage": ["heritage", "history", "historic", "fort", "forts", "palace", "palaces", "architecture", "monument"],
    "culture": ["culture", "cultural", "art", "arts", "dance", "kathakali", "music", "museum", "museums", "crafts"],
    "food": ["food", "foodie", "cuisine", "street food", "cooking", "seafood", "eat"],
    "nature": ["nature", "backwater", "backwaters", "hills", "mountains", "lake", "lakes", "wildlife", "sunset", "gardens"],
    "beaches": ["beach", "beaches", "sea", "island", "islands", "snorkel", "snorkelling", "snorkeling", "scuba", "diving"],
    "adventure": ["adventure", "trek", "trekking", "hike", "hiking", "kayak", "kayaking", "rafting", "safari"],
    "spiritual": ["spiritual", "temple", "temples", "monastery", "ghats", "aarti", "yoga", "church", "churches"],
    "nightlife": ["nightlife", "party", "parties", "bars", "pubs", "clubs"],
    "shopping": ["shopping", "market", "markets", "bazaar", "bazaars", "souvenirs"],
    "wellness": ["wellness", "spa", "ayurveda", "massage"],
}


def _n(tok: str) -> int:
    return int(tok) if tok.isdigit() else WORDS[tok]


def normalise(text: str) -> str:
    t = text.lower().replace("→", " to ").replace("->", " to ").replace("–", "-")
    return re.sub(r"\s+", " ", t).strip()


def parse_nights(t: str) -> int | None:
    if m := re.search(rf"\b{NUM}\s*d(?:ays?)?\s*[/,&]?\s*(?:and\s*)?{NUM}\s*n(?:ights?)?\b", t):
        return _n(m.group(2))
    if m := re.search(rf"\b{NUM}[\s-]*nights?\b", t):
        return _n(m.group(1))
    if m := re.search(rf"\b{NUM}[\s-]*days?\b(?!\s*(?:from now|later|away))", t):
        if not re.search(rf"\bin\s+{re.escape(m.group(0))}", t):
            return max(1, _n(m.group(1)) - 1)
    if re.search(r"\b(a|one)\s+week\b", t):
        return 6
    if re.search(r"\blong weekend\b", t):
        return 3
    if re.search(r"(?<!this )(?<!next )\bweekend\b", t):
        return 2
    return None


_BUDGET_RE = re.compile(
    r"(?P<pre>₹|rs\.?|inr|\$|usd|€|eur|£|gbp|aed|sgd)?\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s?"
    r"(?P<mult>k\b|thousand|lakhs?|lacs?|l\b)?\s?(?P<post>rupees|inr|rs\b|usd|dollars|aed|dirhams|sgd)?")
_CUR = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupees": "INR", "$": "USD", "usd": "USD",
        "dollars": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "aed": "AED", "dirhams": "AED",
        "sgd": "SGD"}


def parse_budget(t: str) -> Money | None:
    for m in _BUDGET_RE.finditer(t):
        raw = m.group("num").replace(",", "")
        if not raw or not re.match(r"^\d+(\.\d+)?$", raw):
            continue
        value = float(raw)
        mult = (m.group("mult") or "").strip()
        cur_tok = (m.group("pre") or m.group("post") or "").strip()
        before = t[max(0, m.start() - 22):m.start()]
        after = t[m.end():m.end() + 12]
        if re.match(r"\s*(days?|nights?|adults?|people|kids?|children|pax|star|am|pm|:|st\b|nd\b|rd\b|th\b)", after):
            continue
        if re.match(rf"\s*{MON}", after):
            continue
        budget_word = re.search(r"(under|below|within|budget|max|upto|up to|less than|around|about|spend|cap)\W*$", before)
        if not (cur_tok or mult or budget_word or value >= 1000):
            continue
        if mult == "k" or mult == "thousand":
            value *= 1000
        elif mult.startswith(("lakh", "lac")) or mult == "l":
            value *= 100000
        if value < 500:
            continue
        return Money.of(value, _CUR.get(cur_tok, "INR"))
    return None


def parse_travellers(t: str) -> Travellers | None:
    adults = children = None
    if m := re.search(rf"\bfamily of {NUM}\b", t):
        n = _n(m.group(1))
        adults, children = (2, n - 2) if n >= 3 else (n, 0)
    if m := re.search(rf"\b{NUM}\s*(?:adults?|people|persons?|pax|travell?ers?|guests|of us)\b", t):
        adults = _n(m.group(1))
    if m := re.search(rf"\bwith\s+{NUM}\s+friends\b", t):
        adults = _n(m.group(1)) + 1
    if m := re.search(rf"\b{NUM}\s*(?:kids?|children|child)\b", t):
        children = _n(m.group(1))
    if adults is None and re.search(r"\b(solo|just me|only me|alone|by myself)\b", t):
        adults = 1
    if adults is None and re.search(
            r"\b(couple|honeymoon|my (wife|husband|partner|girlfriend|boyfriend|fianc[eé]e?)|for two|the two of us)\b", t):
        adults = 2
    if adults is None and children is None:
        return None
    return Travellers(adults=max(1, min(9, adults or 2)), children=min(8, children or 0))


def _resolve(day: int, month: int, today: date) -> date | None:
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    if d < today:
        try:
            d = date(today.year + 1, month, day)
        except ValueError:
            return None
    return d


def parse_dates(t: str, today: date) -> tuple[date | None, int | None, int | None]:
    """Returns (start_date, nights_from_range, month_only)."""
    if m := re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:-|to|till|until)\s*(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s*)?{MON}", t):
        start = _resolve(int(m.group(1)), MONTHS[m.group(3)[:3]], today)
        end = _resolve(int(m.group(2)), MONTHS[m.group(3)[:3]], today)
        if start and end and end > start:
            return start, (end - start).days, None
        return start, None, None
    if m := re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s*)?{MON}\b", t):
        return _resolve(int(m.group(1)), MONTHS[m.group(2)[:3]], today), None, None
    if m := re.search(rf"\b{MON}\s*(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*(?:days?|nights?|people|adults))", t):
        return _resolve(int(m.group(2)), MONTHS[m.group(1)[:3]], today), None, None
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        return _resolve(int(m.group(1)), int(m.group(2)), today), None, None
    if re.search(r"\bday after tomorrow\b", t):
        return today + timedelta(days=2), None, None
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1), None, None
    if m := re.search(r"\b(this|next)\s+weekend\b", t):
        fri = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
        return fri + timedelta(days=7 if m.group(1) == "next" else 0), None, None
    if m := re.search(r"\b(?:next|this|on)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t):
        wd = WEEKDAYS.index(m.group(1))
        return today + timedelta(days=(wd - today.weekday()) % 7 or 7), None, None
    if re.search(r"\bnext month\b", t):
        return None, None, (today.month % 12) + 1
    if m := re.search(rf"\b(?:in|during|this|next|for)\s+{MON}\b", t):
        return None, None, MONTHS[m.group(1)[:3]]
    return None, None, None


def _time(h: int, m: int, ampm: str | None) -> time:
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return time(min(h, 23), min(m, 59))


def parse_constraints(t: str) -> dict:
    c: dict = {}
    if re.search(r"\bno (early|morning)( morning)? flights?\b|\bnot too early\b|\bavoid early\b|\bnothing early\b", t):
        c["depart_after"] = "08:00"
    if m := re.search(r"\b(?:depart|leave|leaving|fly|flight|flights)\w*\s+after\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t):
        c["depart_after"] = _time(int(m.group(1)), int(m.group(2) or 0), m.group(3)).strftime("%H:%M")
    if re.search(r"\bno (red[- ]?eye|late night|late) flights?\b", t):
        c["depart_before"] = "21:00"
    if re.search(r"\b(direct|non[- ]?stop)\b", t):
        c["max_stops"] = 0
    if m := re.search(r"\b([3-5](?:\.\d)?)\s*\+?\s*(?:-\s*)?(?:star|★)", t):
        c["hotel_min_rating"] = float(m.group(1))
    if re.search(r"\b(luxury|premium|splurge|5[- ]star)\b", t):
        c["hotel_tier"] = "premium"
    elif re.search(r"\b(budget (hotel|stay|room)s?|cheap (hotel|stay)s?|backpack\w*)\b", t):
        c["hotel_tier"] = "budget"
    return c


def parse_interests(t: str) -> list[str]:
    found = []
    for tag, words in TAG_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words):
            found.append(tag)
    return found


def parse_places(t: str) -> tuple[str | None, str | None, list[str]]:
    taken: list[tuple[int, int, str]] = []
    for alias in _ALIAS_ORDER:
        for m in re.finditer(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", t):
            if any(not (m.end() <= s or m.start() >= e) for s, e, _ in taken):
                continue
            taken.append((m.start(), m.end(), ALIASES[alias]))
    taken.sort()
    origin = dest = None
    for i, (s, e, code) in enumerate(taken):
        before = t[max(0, s - 22):s]
        if re.search(r"\b(from|leaving|starting|departing|out of|based in|live in|living in)\s*$", before):
            origin = origin or code
        elif re.search(r"\b(to|in|at|visit|visiting|explore|exploring|towards|around|trip|holiday|vacation|getaway|see)\s*$", before):
            if i > 0 and re.fullmatch(r"\s*(to|-)\s*", t[taken[i - 1][1]:s]) and origin is None:
                origin = taken[i - 1][2]
            dest = dest or code
    unlabelled = [c for _, _, c in taken if c not in (origin, dest)]
    for c in unlabelled:
        if dest is None:
            dest = c
        elif origin is None:
            origin = c
    unknown = []
    for m in re.finditer(r"\b(?:to|visit|in)\s+([a-z]{4,}(?:\s[a-z]{4,})?)", t):
        word = m.group(1)
        first = word.split()[0]
        if first in MONTHS or first[:3] in MONTHS or first in ALIASES or any(word.startswith(a) for a in ALIASES):
            continue
        if first in {"the", "this", "next", "with", "december", "january", "budget", "total", "about", "around",
                     "change", "book", "keep", "plan", "make", "have", "early", "morning", "evening", "under"}:
            continue
        if not taken:
            unknown.append(word)
    return origin, dest, unknown


def extract(text: str, today: date) -> IntentPatch:
    t = normalise(text)
    patch = IntentPatch()
    patch.origin, patch.destination, patch.unknown_places = parse_places(t)
    start, nights_from_range, month = parse_dates(t, today)
    patch.start_date, patch.month = start, month
    patch.nights = nights_from_range or parse_nights(t)
    patch.travellers = parse_travellers(t)
    patch.budget = parse_budget(t)
    patch.interests = parse_interests(t)
    if re.search(r"\b(relaxed|slow|chill|laid[- ]back|easy[- ]going|not rushed)\b", t):
        patch.pace = "relaxed"
    elif re.search(r"\b(packed|action[- ]packed|busy|as much as possible|see everything)\b", t):
        patch.pace = "packed"
    patch.constraints = parse_constraints(t)
    if re.search(r"\b(start over|reset|new trip|scratch that)\b", t):
        patch.commands.append("reset")
    if re.search(r"\b(change|different|other|new) (the )?dates?\b", t):
        patch.clear.append("start_date")
    if re.search(r"\b(cheaper|less expensive|lower (the )?cost|save money)\b", t):
        patch.commands.append("cheaper")
    if m := re.search(r"\b(?:go with|choose|pick|select|book) (?:the )?(value|comfort|experience)\b", t):
        patch.commands.append(f"select:{m.group(1)}")
    return patch
