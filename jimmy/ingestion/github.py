import base64
import httpx
from .base import Document, _h


class GitHubIngester:
    def __init__(self, token: str | None = None):
        self.headers = {"Accept": "application/vnd.github+json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def ingest_repo(self, repo: str) -> list[Document]:
        """Ingest README + issues from a GitHub repo. repo = 'owner/name'"""
        docs = []

        r = httpx.get(f"https://api.github.com/repos/{repo}", headers=self.headers, timeout=15)
        r.raise_for_status()
        repo_data = r.json()

        # README
        try:
            r2 = httpx.get(
                f"https://api.github.com/repos/{repo}/readme",
                headers=self.headers, timeout=15,
            )
            if r2.status_code == 200:
                content = base64.b64decode(r2.json()["content"]).decode("utf-8", errors="ignore")
                docs.append(Document(
                    id=f"github_{_h(repo)}_readme",
                    content=content,
                    source="github",
                    title=f"GitHub: {repo} — README",
                    metadata={"type": "readme", "repo": repo, "url": repo_data.get("html_url", "")},
                ))
        except Exception:
            pass

        # Issues (last 100, open + closed)
        try:
            r3 = httpx.get(
                f"https://api.github.com/repos/{repo}/issues",
                headers=self.headers,
                params={"state": "all", "per_page": 100},
                timeout=15,
            )
            if r3.status_code == 200:
                issues = [i for i in r3.json() if not i.get("pull_request")]
                if issues:
                    text = "\n\n".join(
                        f"#{i['number']} {i['title']} [{i.get('state','?')}] {i.get('created_at','')[:10]}\n{i.get('body', '') or ''}"
                        for i in issues
                    )
                    most_recent = max(
                        (i.get("created_at", "")[:10] for i in issues if i.get("created_at")),
                        default="",
                    )
                    docs.append(Document(
                        id=f"github_{_h(repo)}_issues",
                        content=text,
                        source="github",
                        title=f"GitHub: {repo} — Issues",
                        metadata={"type": "issues", "repo": repo, "date": most_recent},
                    ))
        except Exception:
            pass

        # Recent commits (last 50)
        try:
            r4 = httpx.get(
                f"https://api.github.com/repos/{repo}/commits",
                headers=self.headers,
                params={"per_page": 50},
                timeout=15,
            )
            if r4.status_code == 200:
                commits = r4.json()
                if commits:
                    lines = []
                    for c in commits:
                        msg = (c.get("commit", {}).get("message", "") or "").split("\n")[0][:120]
                        date = (c.get("commit", {}).get("author", {}).get("date", "") or "")[:10]
                        author = (c.get("commit", {}).get("author", {}).get("name", "") or "")
                        lines.append(f"{date} [{author}] {msg}")
                    most_recent = commits[0].get("commit", {}).get("author", {}).get("date", "")[:10]
                    docs.append(Document(
                        id=f"github_{_h(repo)}_commits",
                        content=f"Recent commits to {repo}:\n\n" + "\n".join(lines),
                        source="github",
                        title=f"GitHub: {repo} — Commits",
                        metadata={"type": "commits", "repo": repo, "date": most_recent},
                    ))
        except Exception:
            pass

        return docs
