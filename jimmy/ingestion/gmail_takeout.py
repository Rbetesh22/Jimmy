"""Gmail MBOX file ingester (from Google Takeout)."""
import mailbox
import email.utils
import re
from pathlib import Path
from .base import Document, _h


def _decode_payload(msg) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode("utf-8", errors="replace")
                except Exception:
                    pass
        return ""
    try:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


def _strip_quoted(text: str) -> str:
    """Remove quoted reply chains."""
    lines = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^[-_]{3,}", line):
            break
        if re.match(r"^On .+ wrote:$", line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


class GmailTakeoutIngester:
    def ingest(self, mbox_path: str, max_messages: int = 2000) -> list[Document]:
        """Parse Gmail MBOX export from Google Takeout."""
        p = Path(mbox_path)
        if not p.exists():
            raise FileNotFoundError(f"MBOX file not found: {mbox_path}")

        mbox = mailbox.mbox(str(p))
        docs = []
        seen_ids = set()

        for i, msg in enumerate(mbox):
            if i >= max_messages:
                break

            subject = msg.get("Subject", "(no subject)")
            from_addr = msg.get("From", "")
            to_addr = msg.get("To", "")
            date_raw = msg.get("Date", "")
            msg_id = msg.get("Message-ID", "")

            # Deduplicate
            dedup_key = msg_id or f"{subject}_{from_addr}_{date_raw}"
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            # Parse date
            date_iso = ""
            try:
                parsed = email.utils.parsedate_to_datetime(date_raw)
                date_iso = parsed.date().isoformat()
            except Exception:
                pass

            # Extract body
            body = _decode_payload(msg)
            body = _strip_quoted(body)
            if not body or len(body.strip()) < 20:
                continue

            # Skip obvious spam/marketing
            lower_body = body.lower()
            if any(term in lower_body for term in ("unsubscribe", "click here to opt out", "email preferences")):
                if len(body) < 200:
                    continue

            content = (
                f"Subject: {subject}\n"
                f"From: {from_addr}\n"
                f"To: {to_addr}\n"
                f"Date: {date_iso}\n\n"
                f"{body[:2000]}"
            )

            docs.append(Document(
                id=f"gmail_takeout_{_h(dedup_key)}",
                content=content,
                source="gmail",
                title=f"{subject} ({date_iso})",
                metadata={
                    "type": "takeout",
                    "date": date_iso,
                    "from": from_addr[:100],
                    "to": to_addr[:100],
                },
            ))

        return docs
