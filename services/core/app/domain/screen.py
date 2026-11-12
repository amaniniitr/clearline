"""Screen: duplicates and payment safety. Raw measurements only."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from schwifty import IBAN

from .normalise import domain_distance, email_domain, iban_norm, mask_account


def duplicates(vendor_id: str | None, number_norm: str | None, total: Decimal | None, inv_date: date | None,
               related_vendor_ids: list[str], history: list[dict], current_invoice_id: int | None,
               blanket: bool, service_period: str | None) -> dict:
    """history: invoices for related vendors: {id, vendor_id, invoice_number, invoice_number_norm, total, invoice_date, state}."""
    hard = soft = None
    for h in history:
        if h["id"] == current_invoice_id or h.get("state") in ("rejected", "reversed", "filed") or h.get("document_type") not in (None, "invoice"):
            continue
        if h["vendor_id"] in related_vendor_ids and number_norm and h.get("invoice_number_norm") == number_norm:
            hard = h
            break
    if not hard and total is not None and not blanket and not service_period:
        for h in history:
            if h["id"] == current_invoice_id or h["vendor_id"] != vendor_id or h.get("total") is None:
                continue
            if h.get("state") in ("rejected", "filed") or h.get("document_type") not in (None, "invoice"):
                continue
            d = h.get("invoice_date")
            if Decimal(str(h["total"])) == total and d and inv_date and abs((inv_date - d).days) <= 30 \
                    and h.get("invoice_number_norm") != number_norm:
                soft = h
                break
    return {"hard_duplicate": hard is not None, "duplicate_of": hard and hard["invoice_number"],
            "soft_duplicate": soft is not None, "soft_duplicate_of": soft and soft["invoice_number"]}


def payment_safety(invoice_iban: str | None, invoice_account: str | None, banks: list[dict], vendor: dict | None,
                   email_from: str | None, email_reply_to: str | None, paid_before: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    active = [b for b in banks if b.get("active")]
    verified = [b for b in active if b.get("verified_at")]
    present = bool(invoice_iban or invoice_account)
    checksum_ok = None
    if invoice_iban:
        try:
            IBAN(invoice_iban)
            checksum_ok = True
        except Exception:  # noqa: BLE001
            checksum_ok = False
    matches = None
    if present:
        matches = any((invoice_iban and iban_norm(b.get("iban")) == iban_norm(invoice_iban)) or
                      (invoice_account and b.get("account_number") and b["account_number"].replace(" ", "") == invoice_account.replace(" ", ""))
                      for b in verified)
    last_change = max((b["added_at"] for b in active), default=None)
    days = (now - last_change).days if last_change else None
    sender = email_domain(email_from)
    dist, closest, homo = domain_distance(sender, list((vendor or {}).get("domains") or [])) if sender else (None, None, False)
    reply = email_domain(email_reply_to)
    return {
        "bank_present": present, "bank_checksum_valid": checksum_ok, "bank_matches_verified": matches,
        "invoice_bank_masked": mask_account(invoice_iban or invoice_account),
        "verified_bank_masked": mask_account(verified[0].get("iban") or verified[0].get("account_number")) if verified else "none on file",
        "callback_phone": verified[0].get("callback_phone") if verified else None,
        "days_since_bank_change": days, "first_payment": paid_before == 0,
        "sender_domain": sender, "vendor_domain": closest, "sender_domain_distance": dist, "sender_homoglyph": homo,
        "reply_to_mismatch": bool(reply and sender and reply != sender),
    }
