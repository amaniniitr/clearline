"""Typed contracts between stages."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal["invoice", "credit_note", "statement", "proforma", "purchase_order", "other"]
Evidence = Literal["verified", "read", "uncertain", "absent"]
Outcome = Literal["AUTO_APPROVE", "REVIEW", "HOLD", "REJECT", "NOT_AN_INVOICE", "APPLY_CREDIT"]

# Worst wins. Order matters.
OUTCOME_RANK: dict[str, int] = {
    "AUTO_APPROVE": 0, "APPLY_CREDIT": 1, "NOT_AN_INVOICE": 1, "REVIEW": 2, "HOLD": 3, "REJECT": 4,
}


class ExtractedLine(BaseModel):
    description: str
    sku: str | None = None
    qty: str | None = None
    uom: str | None = None
    unit_price: str | None = None
    amount: str | None = None
    quote: str | None = None


class Extraction(BaseModel):
    """What a reader returns. Amounts stay as printed strings; the domain parses them."""
    document_type: DocType = "invoice"
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    po_number: str | None = None
    currency: str | None = None
    subtotal: str | None = None
    tax: str | None = None
    tax_rate: str | None = None
    freight: str | None = None
    total: str | None = None
    tax_mode: Literal["exclusive", "inclusive", "none"] | None = None
    payment_terms: str | None = None
    remit_iban: str | None = None
    remit_account_number: str | None = None
    remit_routing_number: str | None = None
    remit_bank_name: str | None = None
    service_period: str | None = None
    original_invoice_ref: str | None = None
    notes: str | None = None
    lines: list[ExtractedLine] = Field(default_factory=list)
    quotes: dict[str, str] = Field(default_factory=dict)


HEADER_FIELDS = [
    "invoice_number", "invoice_date", "due_date", "vendor_name", "vendor_tax_id", "po_number", "currency",
    "subtotal", "tax", "freight", "total", "payment_terms", "remit_iban", "remit_account_number",
    "remit_routing_number", "remit_bank_name",
]
DECISION_FIELDS = ["vendor_name", "invoice_number", "total", "currency", "po_number"]
PAYMENT_CRITICAL = ["remit_iban", "remit_account_number", "total", "invoice_number", "po_number"]


class FieldResult(BaseModel):
    field: str
    value: str | None
    evidence: Evidence
    reasons: list[str] = Field(default_factory=list)
    readings: dict[str, str | None] = Field(default_factory=dict)
    page: int | None = None
    bbox: list[float] | None = None
    set_by: str = "machine"


class Finding(BaseModel):
    rule_id: str
    status: Literal["pass", "fail"]
    outcome: str | None = None
    owner: str | None = None
    action: str | None = None
    message: str = ""
    tolerance: dict | float | int | str | None = None
    locked: bool = False
    title: str = ""
