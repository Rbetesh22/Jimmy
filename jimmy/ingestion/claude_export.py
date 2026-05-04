"""Claude conversation export ingester."""
import json
from pathlib import Path
from .base import Document, _h


class ClaudeExportIngester:
    def ingest(self, path: str) -> list[Document]:
        """Parse Claude conversation export JSON.

        Claude exports as a JSON array of conversations, each with chat_messages.
        """
        p = Path(path)
        if p.is_dir():
            # Look for conversations.json in folder
            candidates = list(p.glob("*.json"))
            if not candidates:
                raise FileNotFoundError("No JSON files found in directory")
            data = []
            for f in candidates:
                try:
                    parsed = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(parsed, list):
                        data.extend(parsed)
                    elif isinstance(parsed, dict):
                        data.append(parsed)
                except Exception:
                    continue
        else:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                data = [data]

        docs = []
        for conv in data:
            if not isinstance(conv, dict):
                continue

            name = conv.get("name", conv.get("title", "Untitled"))
            uuid = conv.get("uuid", conv.get("id", ""))
            messages = conv.get("chat_messages", conv.get("messages", []))
            created = conv.get("created_at", conv.get("create_time", ""))
            date = created[:10] if len(str(created)) >= 10 else ""

            if not messages:
                continue

            # Build conversation text
            turns = []
            for msg in messages:
                sender = msg.get("sender", msg.get("role", "unknown"))
                # Handle different content formats
                text = ""
                content = msg.get("content", msg.get("text", ""))
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    # Array of content blocks
                    for block in content:
                        if isinstance(block, dict):
                            text += block.get("text", block.get("content", "")) + "\n"
                        elif isinstance(block, str):
                            text += block + "\n"

                text = text.strip()
                if not text:
                    continue

                label = "You" if sender in ("human", "user") else "Claude"
                turns.append(f"**{label}:** {text}")

            if not turns:
                continue

            content = f"Conversation: {name}\nDate: {date}\n\n" + "\n\n".join(turns)

            docs.append(Document(
                id=f"claude_conv_{_h(uuid or name + date)}",
                content=content[:8000],
                source="claude_chat",
                title=f"Claude: {name[:80]}",
                metadata={"type": "conversation", "date": date, "platform": "claude"},
            ))

        return docs
