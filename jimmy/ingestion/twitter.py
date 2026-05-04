import json
import re
import zipfile
import tempfile
import os
from pathlib import Path
from .base import Document, _h

# Strip the JS variable assignment prefix Twitter wraps data in
_JS_PREFIX = re.compile(r'^window\.YTD\.\w+\.part\d+\s*=\s*')


def _parse_tweets_js(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    raw = _JS_PREFIX.sub("", raw)
    return json.loads(raw)


class TwitterIngester:
    def ingest(self, path: str) -> list[Document]:
        """Parse tweets.js from a Twitter/X archive (ZIP or extracted folder)."""
        p = Path(path)
        tmp_dir = None

        if p.suffix == ".zip":
            tmp_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(path, "r") as z:
                z.extractall(tmp_dir)
            # Find tweets.js inside
            tweets_js = None
            for root, _, files in os.walk(tmp_dir):
                for fname in files:
                    if fname == "tweets.js":
                        tweets_js = os.path.join(root, fname)
                        break
                if tweets_js:
                    break
            if not tweets_js:
                raise FileNotFoundError("tweets.js not found in archive")
            target = tweets_js
        elif p.name == "tweets.js":
            target = path
        else:
            # Treat as folder, look for data/tweets.js
            candidates = [
                p / "data" / "tweets.js",
                p / "tweets.js",
            ]
            target = None
            for c in candidates:
                if c.exists():
                    target = str(c)
                    break
            if not target:
                raise FileNotFoundError(f"tweets.js not found in {path}")

        try:
            data = _parse_tweets_js(target)
        finally:
            if tmp_dir:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        docs = []
        for item in data:
            tweet = item.get("tweet", item)
            text = tweet.get("full_text", tweet.get("text", "")).strip()
            if not text or text.startswith("RT @") or len(text) < 30:
                continue
            created = tweet.get("created_at", "")
            tid = tweet.get("id_str", tweet.get("id", ""))
            favorites = tweet.get("favorite_count", 0)
            retweets = tweet.get("retweet_count", 0)

            content = text
            if created:
                content += f"\n\nPosted: {created}"
            if int(favorites or 0) > 0 or int(retweets or 0) > 0:
                content += f"\nEngagement: {favorites} likes, {retweets} retweets"

            tweet_date = ""
            if created:
                try:
                    from datetime import datetime as _dt
                    tweet_date = _dt.strptime(created, "%a %b %d %H:%M:%S +0000 %Y").strftime("%Y-%m-%d")
                except Exception:
                    tweet_date = created[:10] if len(created) >= 10 else ""
            docs.append(Document(
                id=f"twitter_{tid or _h(text)}",
                content=content,
                source="twitter",
                title=f"Tweet: {text[:80]}{'...' if len(text) > 80 else ''}",
                metadata={"type": "tweet", "url": f"https://twitter.com/i/web/status/{tid}" if tid else "", "date": tweet_date},
            ))

        return docs
