#!/usr/bin/env python3
"""
Ingest a Notion export into Jimmy.
Usage: python ingest_notion.py <path_to_notion_export_dir>
"""

import os
import re
import sys
import csv
import time
import json
import http.client
from pathlib import Path

SERVER = "http://localhost:7700"
INGEST_URL = f"{SERVER}/ingest/text"
DELAY = 0.3  # seconds between requests to avoid overwhelming the server

def clean_notion_name(filename: str) -> str:
    """Remove Notion UUID suffix from filename."""
    name = Path(filename).stem
    # Remove trailing UUID (32 hex chars, possibly with spaces/dashes)
    name = re.sub(r'\s+[0-9a-f]{32}$', '', name)
    name = re.sub(r'_[0-9a-f]{32}$', '', name)
    return name.strip()

def clean_markdown(text: str) -> str:
    """Clean Notion markdown: strip image refs, clean up whitespace."""
    # Remove image lines (Notion exports as ![](image.png))
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # Remove Notion property blocks at the top (lines like "Status: Active")
    # Keep the actual content
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def csv_to_text(filepath: Path, title: str) -> str:
    """Convert a Notion database CSV to readable text."""
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            return ""

        lines = [f"# {title}\n"]
        for row in rows:
            # Filter out empty values
            parts = []
            for k, v in row.items():
                if v and v.strip():
                    parts.append(f"{k}: {v.strip()}")
            if parts:
                lines.append("- " + " | ".join(parts))

        return "\n".join(lines)
    except Exception as e:
        print(f"  CSV parse error: {e}")
        return ""

def post_text(title: str, text: str, source_label: str = "notion") -> bool:
    """POST text to /ingest/text."""
    if not text or len(text.strip()) < 50:
        return False

    payload = json.dumps({
        "text": text,
        "source": source_label,
        "title": title
    }).encode('utf-8')

    try:
        conn = http.client.HTTPConnection("127.0.0.1", 7700, timeout=120)
        conn.request("POST", "/ingest/text", body=payload,
                     headers={"Content-Type": "application/json",
                              "Content-Length": str(len(payload))})
        resp = conn.getresponse()
        ok = resp.status == 200
        resp.read()
        conn.close()
        return ok
    except Exception as e:
        print(f"  Error: {e}")
        return False

def ingest_directory(root: Path):
    """Walk the Notion export directory and ingest all content."""
    ingested = 0
    skipped = 0
    errors = 0

    # Collect all files, skip images and _all.csv duplicates
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
            continue
        if path.name.endswith('_all.csv'):
            continue  # Skip _all.csv duplicates
        if path.suffix.lower() not in ('.md', '.csv'):
            continue
        files.append(path)

    print(f"Found {len(files)} content files to ingest\n")

    for path in files:
        title = clean_notion_name(path.name)

        # Build a hierarchical source label from parent folders
        # e.g. "Digital Brain > Columbia > Daily"
        rel = path.relative_to(root)
        parts = list(rel.parts)[:-1]  # exclude filename
        parts = [clean_notion_name(p) for p in parts]
        breadcrumb = " > ".join(parts) if parts else "Notion"

        display = f"{breadcrumb} > {title}" if parts else title

        if path.suffix.lower() == '.md':
            text = path.read_text(encoding='utf-8', errors='replace')
            text = clean_markdown(text)
            # Add title as header if not already present
            if not text.startswith('#'):
                text = f"# {title}\n\n{text}"
        else:  # .csv
            text = csv_to_text(path, title)

        if not text or len(text.strip()) < 50:
            print(f"  SKIP (too short): {display}")
            skipped += 1
            continue

        print(f"  Ingesting: {display} ({len(text):,} chars)...")
        ok = post_text(title=title, text=text, source_label="notion")

        if ok:
            ingested += 1
            print(f"    ✓ done")
        else:
            errors += 1
            print(f"    ✗ failed")

        time.sleep(DELAY)

    print(f"\n{'='*50}")
    print(f"Done: {ingested} ingested, {skipped} skipped, {errors} errors")
    return ingested

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ingest_notion.py <notion_export_dir>")
        sys.exit(1)

    root = Path(sys.argv[1])
    if not root.exists():
        print(f"Directory not found: {root}")
        sys.exit(1)

    print(f"Ingesting Notion export from: {root}")
    print(f"Posting to: {INGEST_URL}\n")

    ingest_directory(root)
