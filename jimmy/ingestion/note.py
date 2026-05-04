import uuid
from datetime import datetime
from .base import Document


class NoteIngester:
    def ingest(self, text: str) -> list[Document]:
        now = datetime.now()
        return [Document(
            id=f"note_{uuid.uuid4().hex[:8]}",
            content=text,
            source="note",
            title=f"Note — {now.strftime('%Y-%m-%d %H:%M')}",
            metadata={
                "type": "note",
                "created_at": now.isoformat(),
            },
        )]
