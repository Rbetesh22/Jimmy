# Jimmy

Personal intelligence system — a second brain that unifies your notes, meetings, courses, emails, and contacts into a searchable, AI-powered knowledge base.

## What it does

- **Ask anything** about your life — Jimmy searches across all your ingested data and answers with citations
- **Network / CRM** — 4,600+ contacts from LinkedIn, Gmail, Apple Contacts, and manual imports with warmth tracking, smart segments, and a triage UI
- **Daily digest** — auto-generated summary of what's relevant today
- **Library** — Goodreads, Kindle highlights, Readwise synced and searchable
- **Timeline, Sparks, Practice** — spaced repetition, idea surfacing, study tools

## Stack

- **Backend**: Python / FastAPI, served via uvicorn
- **Search**: Hybrid retrieval — BM25 keyword + ChromaDB vector (bge-small-en-v1.5) with Reciprocal Rank Fusion
- **LLM**: OpenAI / Anthropic / Ollama fallback chain
- **Storage**: ChromaDB (152K+ chunks), SQLite (contacts/CRM)
- **Frontend**: Single-file vanilla JS web app, SwiftUI iOS app, Chrome extension

## Setup

```bash
# Clone and install
git clone https://github.com/Rbetesh22/Neuron.git jimmy
cd jimmy
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run
jimmy serve        # Start API server on :7700
jimmy ingest all   # Ingest all configured sources
```

Open `http://localhost:7700/app` in your browser.

## Data sources

Google Calendar, Gmail, Google Drive, Apple Notes, Notion, Granola meetings, GoodNotes, Goodreads, Kindle, Readwise, Spotify, YouTube, GitHub, RSS feeds, bookmarks, local files, and more.

## CRM / Network

```bash
jimmy import-fiji path/to/alumni.xlsx
jimmy import-linkedin path/to/connections.csv
jimmy contacts --aff FIJI --region NYC
```

Or use the web UI: Network tab > Triage to rapidly star contacts, then get daily reach-out suggestions for people going stale.

## Project structure

```
jimmy/
  api/server.py      # FastAPI with 40+ endpoints
  retrieval/engine.py # Hybrid search + LLM answering
  storage/store.py    # ChromaDB vector store
  contacts/db.py      # SQLite CRM database
  contacts/importer.py# Contact import (FIJI, LinkedIn, Gmail, macOS)
  ingestion/          # 30+ data source connectors
  ui/index.html       # Single-file web app
  cli.py              # CLI entry point
ios/                  # SwiftUI app
extension/            # Chrome extension
```
