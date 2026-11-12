"""Money and number parsing. Pure. Decimal only, never float.

Locale-aware: handles 1,234.56 (US/UK), 1.234,56 (EU decimal comma), 12,34,567.89 (Indian lakh grouping),
currency symbols/codes, parentheses and trailing minus for negatives.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")

_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}
_CODES = {"USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD", "SGD", "AED", "CHF"}
_ALIASES = {"RS": "INR", "RS.": "INR", "US$": "USD", "DOLLARS": "USD", "RUPEES": "INR"}


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(raw: str | int | float | Decimal | None) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    s = str(raw).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")") or s.endswith("-") or s.startswith("-")
    s = re.sub(r"(?i)\b(USD|EUR|GBP|INR|JPY|CAD|AUD|SGD|AED|CHF|RS\.?)\b", "", s)
    s = re.sub(r"[^\d.,]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        # Whichever separator comes last is the decimal separator.
        if last_comma > last_dot:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif last_comma >= 0:
        # Only commas: decimal comma if exactly 1-2 digits follow the last one ("12,5" / "1.234,56" handled above).
        tail = s[last_comma + 1:]
        if len(tail) in (1, 2) and s.count(",") == 1:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")  # thousands (US) or lakh grouping (IN)
    elif s.count(".") > 1:
        s = s.replace(".", "")  # 1.234.567 (EU thousands)
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if neg else v


def parse_currency(raw: str | None, fallback: str | None = None) -> str | None:
    if not raw:
        return fallback
    s = raw.strip().upper()
    if s in _CODES:
        return s
    if s in _ALIASES:
        return _ALIASES[s]
    for sym, code in _SYMBOLS.items():
        if sym in raw:
            return code
    m = re.search(r"\b([A-Z]{3})\b", s)
    if m and m.group(1) in _CODES:
        return m.group(1)
    return fallback


_DATE_FORMATS = ["%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d.%m.%Y", "%d. %B %Y"]
_MONTHS = {  # German, Dutch, French → English (strptime is English-only)
    "januar": "January", "januari": "January", "janvier": "January", "februar": "February", "februari": "February",
    "février": "February", "fevrier": "February", "märz": "March", "maerz": "March", "maart": "March", "mars": "March",
    "april": "April", "avril": "April", "mai": "May", "mei": "May", "juni": "June", "juin": "June", "juli": "July",
    "juillet": "July", "august": "August", "augustus": "August", "août": "August", "aout": "August", "september": "September",
    "septembre": "September", "oktober": "October", "octobre": "October", "november": "November", "novembre": "November",
    "dezember": "December", "december": "December", "décembre": "December", "decembre": "December",
}


def _english_months(s: str) -> str:
    return re.sub(r"[A-Za-zÀ-ÿ]+", lambda m: _MONTHS.get(m.group(0).lower(), m.group(0)), s)


def parse_date(raw: str | None, dayfirst: bool | None = None) -> tuple[date | None, bool]:
    """Returns (date, ambiguous). 03/04/2026 is ambiguous (March 4 or April 3) and is flagged, never guessed silently —
    unless the caller passes the document's locale convention (dayfirst=True for EU/IN/UK, False for US)."""
    if not raw:
        return None, False
    s = _english_months(raw.strip())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date(), False
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            if a > 12:
                return date(y, b, a), False
            if b > 12:
                return date(y, a, b), False
            if dayfirst is not None and a != b:
                return (date(y, b, a) if dayfirst else date(y, a, b)), False
            # both <= 12 and no locale hint: month-first guess, flagged as ambiguous
            return date(y, a, b), a != b
        except ValueError:
            return None, False
    return None, False


def fmt_money(x: Decimal | None, currency: str | None = "USD") -> str:
    if x is None:
        return "—"
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}.get(currency or "USD", "")
    sign = "-" if x < 0 else ""
    v = abs(q2(x))
    if currency == "INR":  # lakh grouping: ₹11,56,400.00
        whole, frac = f"{v:.2f}".split(".")
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        return f"{sign}₹{','.join(groups + [tail])}.{frac}"
    return f"{sign}{sym}{v:,.2f}" if sym else f"{sign}{v:,.2f} {currency}"


# Static demo FX rates to the base currency (USD) for policy limits. Production would use a daily rate feed.
FX_TO_USD = {"USD": Decimal("1"), "EUR": Decimal("1.08"), "GBP": Decimal("1.27"), "INR": Decimal("0.0119"),
             "CAD": Decimal("0.73"), "AUD": Decimal("0.66"), "SGD": Decimal("0.75"), "AED": Decimal("0.27"), "CHF": Decimal("1.13")}


def to_usd(x: Decimal | None, currency: str | None) -> Decimal | None:
    if x is None:
        return None
    return q2(x * FX_TO_USD.get(currency or "USD", Decimal("1")))
