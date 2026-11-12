"""Normalisation and identity helpers. Pure."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz.distance import Levenshtein

_LEGAL_SUFFIXES = r"\b(inc|incorporated|llc|ltd|limited|pvt|private|plc|corp|corporation|co|company|gmbh|sa|bv)\b\.?"

# Characters commonly swapped in lookalike domains.
_HOMOGLYPHS = str.maketrans({"0": "o", "1": "l", "3": "e", "5": "s", "і": "i", "о": "o", "а": "a", "е": "e"})


def invoice_number_norm(raw: str | None) -> str | None:
    """'INV-0042', 'inv 42', '#42' and '42' all normalise to '42'. Letters inside the number are kept."""
    if not raw:
        return None
    s = raw.upper().strip()
    s = re.sub(r"^(INVOICE|INV|BILL|NO|NUMBER|#)[\s.#:\-]*", "", s)
    s = re.sub(r"[\s\-_/.#]", "", s)
    s = s.lstrip("0") or "0"
    return s


def po_norm(raw: str | None) -> str | None:
    if not raw:
        return None
    m = re.search(r"(\d{3,})", raw)
    return f"PO-{m.group(1)}" if m else raw.strip().upper()


def name_norm(raw: str | None) -> str:
    if not raw:
        return ""
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(_LEGAL_SUFFIXES, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def email_domain(addr: str | None) -> str | None:
    if not addr:
        return None
    m = re.search(r"@([\w.\-]+)", addr)
    return m.group(1).lower() if m else None


def domain_distance(sender: str | None, known: list[str]) -> tuple[int | None, str | None, bool]:
    """(edit distance to closest known domain, that domain, homoglyph_match).
    0 = exact. A small non-zero distance is the lookalike signal."""
    if not sender or not known:
        return None, None, False
    best = min(known, key=lambda k: Levenshtein.distance(sender, k))
    d = Levenshtein.distance(sender, best)
    homo = d > 0 and sender.replace("rn", "m").translate(_HOMOGLYPHS) == best.replace("rn", "m").translate(_HOMOGLYPHS)
    return d, best, homo


def iban_norm(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"\s", "", raw).upper()
    return s or None


def mask_account(raw: str | None) -> str:
    if not raw:
        return "—"
    s = re.sub(r"\s", "", raw)
    return f"••••{s[-4:]}" if len(s) > 4 else "••••"


_SPEC = re.compile(r"(?i)\b(m\d+|\d+(?:\.\d+)?\s?(?:mm|cm|m|kg|g|lb|lbs|in|inch|ft|l|ml|v|w|kw|gb|tb)?)\b")


def spec_tokens(desc: str) -> set[str]:
    """Numbers + units in a description (M8, 6 mm, 25kg). Used by the deterministic pairing veto."""
    return {re.sub(r"\s", "", t).lower() for t in _SPEC.findall(desc or "") if re.search(r"\d", t)}
