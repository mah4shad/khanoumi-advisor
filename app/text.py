"""Small Persian text helpers: digit normalization and budget extraction."""

from __future__ import annotations

import re

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_WORDS = {
    "یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5,
    "شش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10,
}
_UNITS = {"میلیون": 1_000_000, "هزار": 1_000}
_NUM = r"(?<![\u0600-\u06FF\d.])(\d+(?:\.\d+)?|" + "|".join(_WORDS) + r")"
_TOKEN = re.compile(_NUM + r"\s*(میلیون|هزار)?\s*(تومان|تومن)?")

MIN_BUDGET = 10_000
MAX_BUDGET = 200_000_000

COMPARE_WORDS = ("مقایسه", "فرق", "کدوم بهتر", "کدام بهتر", "کدومشون")


def normalize(text: str | None) -> str:
    """ASCII digits, Persian ی/ک, no thousands separators, single spaces."""
    text = (text or "").translate(_DIGITS)
    text = re.sub(r"(?<=\d)[,،٬](?=\d)", "", text)
    text = text.replace("ي", "ی").replace("ك", "ک")
    return " ".join(text.split())


def parse_budget(text: str | None) -> int | None:
    """Find a toman budget inside free text, e.g. «زیر ۵۰۰ هزار تومان» -> 500000."""
    text = normalize(text)
    matches = list(_TOKEN.finditer(text))
    best: int | None = None
    i = 0
    while i < len(matches):
        total, unit_seen, toman_seen = 0, False, False
        j = i
        while j < len(matches):
            m = matches[j]
            raw, unit, toman = m.group(1), m.group(2), m.group(3)
            value = _WORDS[raw] if raw in _WORDS else float(raw)
            total += value * _UNITS.get(unit or "", 1)
            unit_seen = unit_seen or bool(unit)
            toman_seen = toman_seen or bool(toman)
            nxt = matches[j + 1] if j + 1 < len(matches) else None
            # «۲ میلیون و ۵۰۰ هزار تومان» -> sum the pieces joined by «و»
            if nxt and text[m.end():nxt.start()].strip() == "و" and unit:
                j += 1
                continue
            break
        amount = int(total)
        if (unit_seen or toman_seen or amount >= MIN_BUDGET) and MIN_BUDGET <= amount <= MAX_BUDGET:
            best = amount
            break
        i = j + 1
    return best


def wants_comparison(text: str | None) -> bool:
    text = normalize(text)
    return any(word in text for word in COMPARE_WORDS)
