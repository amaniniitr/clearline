"""Intake: store the file once (sha256), sniff the type, unpack .eml (attachment + headers + body)."""
from __future__ import annotations

import email
import hashlib
from email import policy
from pathlib import Path

from .. import db
from ..config import settings

MAGIC = {b"%PDF": "application/pdf", b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png"}


class IntakeError(Exception):
    pass


def sniff(data: bytes) -> str | None:
    for sig, mime in MAGIC.items():
        if data.startswith(sig):
            return mime
    return None


def ingest_bytes(data: bytes, filename: str, channel: str = "upload", scenario: str | None = None) -> tuple[int, bool]:
    """Returns (document_id, is_redelivery)."""
    s = settings()
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise IntakeError(f"file larger than {s.max_upload_mb} MB")
    meta = {"email_from": None, "email_reply_to": None, "email_subject": None, "email_body": None}
    mime = sniff(data)
    if mime is None and (filename.lower().endswith(".eml") or data[:200].lower().find(b"from:") >= 0):
        msg = email.message_from_bytes(data, policy=policy.default)
        meta = {"email_from": str(msg.get("From") or ""), "email_reply_to": str(msg.get("Reply-To") or "") or None,
                "email_subject": str(msg.get("Subject") or ""), "email_body": None}
        body = msg.get_body(preferencelist=("plain",))
        meta["email_body"] = body.get_content() if body else None
        att = next((p for p in msg.iter_attachments() if sniff(p.get_payload(decode=True) or b"")), None)
        if att is None:
            raise IntakeError("email has no PDF or image attachment")
        data, filename = att.get_payload(decode=True), att.get_filename() or "attachment.pdf"
        mime = sniff(data)
        channel = "email"
    if mime is None:
        raise IntakeError("unsupported file type (PDF, JPEG, PNG or .eml)")
    sha = hashlib.sha256(data + (meta["email_from"] or "").encode()).hexdigest()
    existing = db.q1("SELECT id FROM documents WHERE sha256=%s", (sha,))
    if existing:
        return existing["id"], True
    ext = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}[mime]
    path = s.storage_dir / f"{sha[:16]}{ext}"
    path.write_bytes(data)
    r = db.q1("""INSERT INTO documents (sha256, filename, mime, channel, email_from, email_reply_to, email_subject, email_body,
                 storage_path, scenario) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
              (sha, filename, mime, channel, meta["email_from"], meta["email_reply_to"], meta["email_subject"], meta["email_body"],
               str(path), scenario))
    return r["id"], False


def ingest_path(p: Path, scenario: str | None = None) -> tuple[int, bool]:
    return ingest_bytes(p.read_bytes(), p.name, "scenario", scenario)
