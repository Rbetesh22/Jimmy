"""Enhanced GitHub repos ingester — clones/pulls repos, extracts READMEs + recent diffs."""
import subprocess
import tempfile
import os
from pathlib import Path
from .base import Document, _h


class GitHubReposIngester:
    def __init__(self, token: str | None = None):
        self.token = token
        self.headers = {"Accept": "application/vnd.github+json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def ingest(self, repos: list[str] | None = None) -> list[Document]:
        """Ingest GitHub repos. If repos is None, fetches user's recent repos.

        repos: list of 'owner/repo' strings, or None for auto-discovery
        """
        if repos is None:
            repos = self._discover_repos()

        docs = []
        for repo in repos:
            try:
                docs.extend(self._ingest_repo(repo))
            except Exception as e:
                print(f"  GitHub: {repo} failed — {e}")
        return docs

    def _discover_repos(self) -> list[str]:
        """Fetch user's repos via gh CLI or API."""
        import httpx
        try:
            # Try gh CLI first (uses existing auth)
            result = subprocess.run(
                ["gh", "repo", "list", "--limit", "30", "--json", "nameWithOwner",
                 "--jq", ".[].nameWithOwner"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split("\n")[:30]
        except Exception:
            pass

        # Fall back to API
        if self.token:
            r = httpx.get(
                "https://api.github.com/user/repos",
                headers=self.headers,
                params={"sort": "pushed", "per_page": 30},
                timeout=15,
            )
            if r.status_code == 200:
                return [repo["full_name"] for repo in r.json()]
        return []

    def _ingest_repo(self, repo: str) -> list[Document]:
        """Ingest a single repo: README + recent diffs + key files."""
        import httpx
        docs = []

        # README via API
        try:
            import base64
            r = httpx.get(
                f"https://api.github.com/repos/{repo}/readme",
                headers=self.headers, timeout=15,
            )
            if r.status_code == 200:
                content = base64.b64decode(r.json()["content"]).decode("utf-8", errors="ignore")
                docs.append(Document(
                    id=f"github_{_h(repo)}_readme",
                    content=content,
                    source="github",
                    title=f"GitHub: {repo} — README",
                    metadata={"type": "readme", "repo": repo},
                ))
        except Exception:
            pass

        # Recent commits + diffs via API
        try:
            r = httpx.get(
                f"https://api.github.com/repos/{repo}/commits",
                headers=self.headers,
                params={"per_page": 20},
                timeout=15,
            )
            if r.status_code == 200:
                commits = r.json()
                if commits:
                    lines = []
                    for c in commits:
                        msg = (c.get("commit", {}).get("message", "") or "").split("\n")[0][:120]
                        date = (c.get("commit", {}).get("author", {}).get("date", "") or "")[:10]
                        author = c.get("commit", {}).get("author", {}).get("name", "")
                        lines.append(f"{date} [{author}] {msg}")

                    most_recent = commits[0].get("commit", {}).get("author", {}).get("date", "")[:10]
                    docs.append(Document(
                        id=f"github_{_h(repo)}_commits",
                        content=f"Recent commits to {repo}:\n\n" + "\n".join(lines),
                        source="github",
                        title=f"GitHub: {repo} — Commits",
                        metadata={"type": "commits", "repo": repo, "date": most_recent},
                    ))

                    # Get diff for most recent commit
                    try:
                        sha = commits[0].get("sha", "")
                        if sha:
                            r2 = httpx.get(
                                f"https://api.github.com/repos/{repo}/commits/{sha}",
                                headers=self.headers, timeout=15,
                            )
                            if r2.status_code == 200:
                                files = r2.json().get("files", [])
                                diff_lines = []
                                for f in files[:20]:
                                    fname = f.get("filename", "")
                                    patch = f.get("patch", "")
                                    if patch:
                                        diff_lines.append(f"--- {fname} ---\n{patch[:500]}")
                                if diff_lines:
                                    docs.append(Document(
                                        id=f"github_{_h(repo)}_latest_diff",
                                        content=f"Latest changes to {repo} ({most_recent}):\n\n" + "\n\n".join(diff_lines),
                                        source="github",
                                        title=f"GitHub: {repo} — Latest Diff",
                                        metadata={"type": "diff", "repo": repo, "date": most_recent},
                                    ))
                    except Exception:
                        pass
        except Exception:
            pass

        # Issues (open, last 50)
        try:
            r = httpx.get(
                f"https://api.github.com/repos/{repo}/issues",
                headers=self.headers,
                params={"state": "open", "per_page": 50},
                timeout=15,
            )
            if r.status_code == 200:
                issues = [i for i in r.json() if not i.get("pull_request")]
                if issues:
                    text = "\n\n".join(
                        f"#{i['number']} {i['title']} [{i.get('state', '?')}] {i.get('created_at', '')[:10]}\n{(i.get('body', '') or '')[:200]}"
                        for i in issues
                    )
                    docs.append(Document(
                        id=f"github_{_h(repo)}_issues",
                        content=text,
                        source="github",
                        title=f"GitHub: {repo} — Issues",
                        metadata={"type": "issues", "repo": repo},
                    ))
        except Exception:
            pass

        return docs
