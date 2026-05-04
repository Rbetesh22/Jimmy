"""Google Drive ingester.

Fetches Docs, Sheets, and Slides that you own or have recently edited.
Exports content as plain text. Owned docs are highest signal (your writing).
"""
import re
from datetime import datetime, timezone, timedelta
from .base import Document


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


MIME_TYPES = {
    "application/vnd.google-apps.document":     ("text/plain",        ".txt"),
    "application/vnd.google-apps.spreadsheet":  ("text/csv",          ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain",        ".txt"),
}


class GoogleDriveIngester:
    def __init__(self, credentials, account_label: str = "primary"):
        from googleapiclient.discovery import build
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.account_label = account_label

    def ingest(self, days: int = 365, owned_only: bool = False) -> list[Document]:
        docs = []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        mime_filter = " or ".join(f"mimeType='{m}'" for m in MIME_TYPES)
        query = f"({mime_filter}) and modifiedTime > '{cutoff}' and trashed = false"
        if owned_only:
            query += " and 'me' in owners"

        files = self._list_files(query)
        print(f"  [{self.account_label}] {len(files)} Drive files")

        for f in files:
            try:
                doc = self._file_to_doc(f)
                if doc:
                    docs.append(doc)
            except Exception as e:
                print(f"    Drive '{f.get('name', '')}': {e}")

        return docs

    def _list_files(self, query: str) -> list[dict]:
        files = []
        page_token = None
        while True:
            resp = self.service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, owners, webViewLink)",
                pageSize=200,
                pageToken=page_token,
            ).execute()
            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    def _file_to_doc(self, f: dict) -> Document | None:
        file_id   = f["id"]
        name      = f.get("name", "Untitled")
        mime      = f.get("mimeType", "")
        modified  = f.get("modifiedTime", "")[:10]
        link      = f.get("webViewLink", "")
        owners    = [o.get("displayName") or o.get("emailAddress", "") for o in f.get("owners", [])]
        owned     = bool(owners)

        export_mime, _ = MIME_TYPES.get(mime, (None, None))
        if not export_mime:
            return None

        resp = self.service.files().export(fileId=file_id, mimeType=export_mime).execute()
        if isinstance(resp, bytes):
            content = resp.decode("utf-8", errors="replace")
        else:
            content = str(resp)

        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        if len(content) < 50:
            return None

        doc_type = mime.split(".")[-1]  # "document", "spreadsheet", "presentation"
        owner_str = f"Owner: {', '.join(owners)}\n" if owned else ""

        full_content = (
            f"Document: {name}\n"
            f"Type: Google {doc_type.capitalize()}\n"
            f"{owner_str}"
            f"Last modified: {modified}\n\n"
            f"{content[:3000]}"
        )

        return Document(
            id=f"gdrive_{self.account_label}_{file_id}",
            content=full_content,
            source="gdrive",
            title=f"{name} (Drive)",
            metadata={
                "type": doc_type,
                "date": modified,
                "url": link,
                "account": self.account_label,
            },
        )
