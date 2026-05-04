"""ChatGPT conversation export ingester."""
import json
from pathlib import Path
from datetime import datetime
from .base import Document, _h


class ChatGPTExportIngester:
    def ingest(self, path: str) -> list[Document]:
        """Parse ChatGPT export (conversations.json).

        ChatGPT exports contain a 'mapping' dict with message nodes.
        """
        p = Path(path)
        if p.is_dir():
            conv_file = p / "conversations.json"
            if not conv_file.exists():
                raise FileNotFoundError("conversations.json not found in directory")
            p = conv_file

        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, list):
            data = [data]

        docs = []
        for conv in data:
            if not isinstance(conv, dict):
                continue

            title = conv.get("title", "Untitled")
            conv_id = conv.get("id", conv.get("conversation_id", ""))
            create_time = conv.get("create_time", 0)
            mapping = conv.get("mapping", {})

            date = ""
            if create_time:
                try:
                    date = datetime.fromtimestamp(float(create_time)).strftime("%Y-%m-%d")
                except Exception:
                    pass

            if not mapping:
                continue

            # Extract messages in order
            turns = []
            for node_id, node in mapping.items():
                msg = node.get("message")
                if not msg:
                    continue
                role = msg.get("author", {}).get("role", "")
                if role not in ("user", "assistant"):
                    continue
                content = msg.get("content", {})
                parts = content.get("parts", [])
                text = ""
                for part in parts:
                    if isinstance(part, str):
                        text += part
                    elif isinstance(part, dict):
                        text += part.get("text", "")

                text = text.strip()
                if not text:
                    continue

                label = "You" if role == "user" else "ChatGPT"
                turns.append(f"**{label}:** {text}")

            if not turns:
                continue

            content = f"Conversation: {title}\nDate: {date}\n\n" + "\n\n".join(turns)

            docs.append(Document(
                id=f"chatgpt_conv_{_h(conv_id or title + date)}",
                content=content[:8000],
                source="chatgpt_chat",
                title=f"ChatGPT: {title[:80]}",
                metadata={"type": "conversation", "date": date, "platform": "chatgpt"},
            ))

        return docs
