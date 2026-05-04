"""AI coding session logs ingester — Cursor, Claude Code, Codex sessions."""
import json
import re
from pathlib import Path
from .base import Document, _h


class CodingSessionsIngester:
    def ingest(self, path: str) -> list[Document]:
        """Ingest AI coding session logs from various tools.

        Supports:
        - Claude Code JSONL transcripts (~/.claude/projects/*/...)
        - Cursor chat logs
        - Generic JSON/JSONL conversation logs
        """
        p = Path(path)
        docs = []

        if p.is_dir():
            # Scan directory for session files
            for f in sorted(p.rglob("*.jsonl")):
                docs.extend(self._parse_jsonl(f))
            for f in sorted(p.rglob("*.json")):
                try:
                    docs.extend(self._parse_json(f))
                except Exception:
                    pass
        elif p.suffix == ".jsonl":
            docs.extend(self._parse_jsonl(p))
        elif p.suffix == ".json":
            docs.extend(self._parse_json(p))
        else:
            raise ValueError(f"Unsupported file type: {p.suffix}")

        return docs

    def ingest_claude_code(self) -> list[Document]:
        """Auto-discover Claude Code session transcripts."""
        claude_dir = Path.home() / ".claude" / "projects"
        if not claude_dir.exists():
            return []

        docs = []
        for jsonl_file in sorted(claude_dir.rglob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:50]:
            docs.extend(self._parse_jsonl(jsonl_file))
        return docs

    def _parse_jsonl(self, path: Path) -> list[Document]:
        """Parse JSONL transcript (Claude Code format)."""
        docs = []
        turns = []
        session_date = ""

        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                role = entry.get("role", entry.get("type", ""))
                content = entry.get("content", "")
                timestamp = entry.get("timestamp", entry.get("ts", ""))

                if not session_date and timestamp:
                    session_date = str(timestamp)[:10]

                if isinstance(content, str) and content.strip():
                    text = content.strip()
                elif isinstance(content, list):
                    text = " ".join(
                        block.get("text", "") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ).strip()
                else:
                    continue

                if not text or len(text) < 10:
                    continue

                label = "User" if role in ("human", "user") else "Assistant"
                turns.append(f"**{label}:** {text[:1000]}")
        except Exception:
            return []

        if not turns:
            return []

        # Create document from session
        title = f"Coding session: {path.stem[:50]}"

        # Try to extract what the session was about from first user message
        for turn in turns[:3]:
            if turn.startswith("**User:**"):
                summary = turn[9:].strip()[:100]
                title = f"Code: {summary}"
                break

        content = f"Session: {path.name}\nDate: {session_date}\n\n" + "\n\n".join(turns[:50])

        docs.append(Document(
            id=f"coding_{_h(str(path))}",
            content=content[:8000],
            source="coding_session",
            title=title[:120],
            metadata={"type": "coding_session", "date": session_date, "tool": "claude_code", "path": str(path)},
        ))
        return docs

    def _parse_json(self, path: Path) -> list[Document]:
        """Parse JSON conversation log (Cursor, generic formats)."""
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))

        # Handle array of conversations
        if isinstance(data, list):
            convos = data
        elif isinstance(data, dict):
            convos = [data]
        else:
            return []

        docs = []
        for conv in convos:
            if not isinstance(conv, dict):
                continue

            title = conv.get("title", conv.get("name", path.stem))
            messages = conv.get("messages", conv.get("turns", conv.get("chat", [])))
            date = str(conv.get("created_at", conv.get("timestamp", "")))[:10]

            if not messages:
                continue

            turns = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", msg.get("sender", ""))
                text = msg.get("content", msg.get("text", ""))
                if isinstance(text, str) and text.strip():
                    label = "User" if role in ("user", "human") else "Assistant"
                    turns.append(f"**{label}:** {text[:1000]}")

            if not turns:
                continue

            content = f"Session: {title}\nDate: {date}\n\n" + "\n\n".join(turns[:50])
            docs.append(Document(
                id=f"coding_{_h(title + date)}",
                content=content[:8000],
                source="coding_session",
                title=f"Code: {title[:80]}",
                metadata={"type": "coding_session", "date": date, "path": str(path)},
            ))

        return docs
