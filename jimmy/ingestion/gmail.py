"""Gmail ingester.

Fetches sent mail, starred messages, and important/flagged threads.
Sent mail is highest signal (reflects your thinking and decisions).
Received mail is included for threads you've participated in.

Multiple accounts supported via google_auth.py.
"""
import base64
import re
from datetime import datetime, timezone, timedelta
from email import message_from_bytes
from .base import Document


def _decode_body(payload: dict) -> str:
    """Recursively extract text/plain from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data", "")

    if mime_type == "text/plain" and data:
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""

    # Recurse into multipart
    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text
    return ""


def _strip_quoted(text: str) -> str:
    """Remove quoted reply chains (lines starting with > or ---Original---)."""
    lines = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^[-_]{3,}", line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


class GmailIngester:
    def __init__(self, credentials, account_label: str = "primary"):
        from googleapiclient.discovery import build
        self.service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        self.account_label = account_label

    def ingest(self, days: int = 60) -> list[Document]:
        docs = []
        cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

        # Sent mail — reflects your decisions, responses, thinking
        docs.extend(self._fetch_label("SENT", cutoff_ms, max_messages=300))
        # Starred — things you explicitly flagged
        docs.extend(self._fetch_label("STARRED", cutoff_ms, max_messages=200))

        # Deduplicate by message ID
        seen: set[str] = set()
        unique = []
        for d in docs:
            if d.id not in seen:
                seen.add(d.id)
                unique.append(d)

        print(f"  [{self.account_label}] {len(unique)} Gmail messages")
        return unique

    def _fetch_label(self, label: str, cutoff_ms: int, max_messages: int = 200) -> list[Document]:
        docs = []
        page_token = None
        after_date = datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc).strftime("%Y/%m/%d")
        query = f"after:{after_date}"

        while len(docs) < max_messages:
            kwargs = {
                "userId": "me",
                "labelIds": [label],
                "q": query,
                "maxResults": min(100, max_messages - len(docs)),
            }
            if page_token:
                kwargs["pageToken"] = page_token

            resp = self.service.users().messages().list(**kwargs).execute()
            messages = resp.get("messages", [])
            if not messages:
                break

            for msg_ref in messages:
                try:
                    doc = self._message_to_doc(msg_ref["id"], label)
                    if doc:
                        docs.append(doc)
                except Exception:
                    pass

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return docs

    def _message_to_doc(self, msg_id: str, label: str) -> Document | None:
        msg = self.service.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()

        headers = msg.get("payload", {}).get("headers", [])
        subject = _header(headers, "Subject") or "(no subject)"
        from_addr = _header(headers, "From")
        to_addr = _header(headers, "To")
        date_str = _header(headers, "Date")
        snippet = msg.get("snippet", "")

        # Parse date
        date_iso = ""
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            date_iso = dt.date().isoformat()
        except Exception:
            pass

        # Extract body — prefer plain text, strip quoted parts
        body = _decode_body(msg.get("payload", {}))
        body = _strip_quoted(body)
        if not body:
            body = snippet

        if not body or len(body.strip()) < 20:
            return None

        content = (
            f"Subject: {subject}\n"
            f"From: {from_addr}\n"
            f"To: {to_addr}\n"
            f"Date: {date_iso}\n\n"
            f"{body[:1500]}"
        )

        return Document(
            id=f"gmail_{self.account_label}_{msg_id}",
            content=content,
            source="gmail",
            title=f"{subject} ({date_iso})",
            metadata={
                "type": label.lower(),
                "date": date_iso,
                "from": from_addr,
                "to": to_addr,
                "account": self.account_label,
            },
        )
