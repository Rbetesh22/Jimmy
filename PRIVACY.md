# Neuron — Privacy & Data Flow Reference

This document is an honest breakdown of what data leaves your machine, what
third-party services are connected, and how to revoke access.

---

## What gets sent to Anthropic Claude

Every time you use /ask, /ask/stream, /digest, /context, /resurface,
/connections, /spark, /analogies, /practice, /node/summary, /graph/build,
/suggestions, /recommendations, or similar AI-powered endpoints, your content
is sent to the Anthropic API (api.anthropic.com).

Specifically, Claude receives:
- Chunks of your stored notes, documents, and highlights retrieved as context
  for your query (up to ~25 chunks per request, each up to ~1000 chars after
  the sanitize_chunk filter).
- The text of your question or topic.
- Calendar event titles and dates (upcoming events only, via upcoming_summary).
- Your first name ("Ralph") and institution ("Columbia CS student") hardcoded
  in system prompts so Claude can respond in context.
- Query expansion calls: for complex queries, a short prompt containing your
  question is sent to claude-haiku to generate alternative search phrasings
  before any content is retrieved.

What Claude does NOT receive:
- Your raw .env secrets or API tokens.
- Full email bodies (Gmail ingest stores summaries/subjects; full content
  chunks are sent through sanitize_chunk which redacts [email] addresses and
  [phone] numbers before being included in prompts).
- Google OAuth tokens or refresh tokens.
- Your Twitter/X password (used only locally for the twscrape session).

Anthropic's data handling: https://www.anthropic.com/privacy
Claude API data is not used to train models by default (API terms).

---

## What third-party services are connected

| Service | What it accesses | OAuth scope / access level |
|---|---|---|
| Anthropic Claude | Chunks of your KB content, your questions | API key — no OAuth |
| OpenAI (optional) | Same as Claude — fallback if no Anthropic key | API key |
| Google Calendar | Your calendar events (read-only) | calendar.readonly |
| Gmail | Your email threads (read-only) | gmail.readonly |
| Google Drive | Your Docs, Sheets, Slides (read-only) | drive.readonly |
| Canvas LMS | Your courses, assignments, submissions, announcements | Canvas API token (read) |
| Notion | Your pages and databases | Notion integration token (read) |
| Readwise | Your highlights and books | Readwise token (read) |
| GitHub | Your repositories and code | GitHub personal access token |
| Spotify | Your listening history and playlists | Spotify OAuth (read) |
| Pocket | Your saved articles | Pocket consumer key + access token |
| Trakt | Your watched movies/TV | Trakt client ID (read-only public) |
| WHOOP | Your health/fitness data | WHOOP OAuth |
| Twitter/X | Your timeline (scraping via twscrape) | Username + password stored in .env |
| Jina.ai (r.jina.ai) | Web pages fetched for web-search context | No auth — public reader API |
| DuckDuckGo | Search queries when web search is triggered | No auth — public HTML endpoint |

All Google access uses read-only OAuth scopes — the app cannot modify, delete,
or send anything in your Google account.

---

## What is stored locally

All data is stored in ~/.neuron/ (never uploaded anywhere by default):

- ~/.neuron/chroma/ — ChromaDB vector database containing all ingested content
  chunks and their embeddings. Not encrypted at rest. Protected only by macOS
  filesystem permissions.
- ~/.neuron/google_token_<email>.json — Google OAuth refresh tokens. Sensitive.
  Do not commit these. They are listed in .gitignore.
- ~/.neuron/digest_cache.json and other *_cache.json files — AI-generated
  summaries cached locally. Contain excerpts of your content.
- ~/.neuron/twscrape_pool.db — Twitter session pool (if Twitter is configured).
- ~/.neuron/srs_data.json — Spaced repetition review history.
- ~/neuron/.env — All API keys and secrets. Listed in .gitignore. Never commit.

The ChromaDB data is NOT encrypted at rest. On a personal Mac with FileVault
disk encryption enabled, this is adequately protected. If you share your
machine or have it managed by an institution, be aware that ~/.neuron/chroma
contains the full text of your notes, emails, and documents.

---

## API server security

The server binds to 127.0.0.1 by default (localhost only) when started with
`neuron serve`. It is NOT exposed to the network unless you explicitly pass
`--host 0.0.0.0`.

CORS is restricted to:
- http://localhost:7700
- http://127.0.0.1:7700
- chrome-extension://* (for the Neuron browser extension)

API key authentication is available. Set NEURON_API_KEY in .env. When set,
all sensitive endpoints require the header X-API-Key: <your-key>. The /health,
/app, /manifest.json, and /graph-ui endpoints remain public (no personal data).

---

## How to revoke access to each service

Google (Calendar, Gmail, Drive):
  - Go to myaccount.google.com/permissions
  - Find your OAuth app and click "Remove Access"
  - Delete ~/.neuron/google_token_*.json

Canvas:
  - Log in to Canvas > Account > Settings > Approved Integrations
  - Delete the token, or regenerate it and update .env

Notion:
  - Go to notion.so/my-integrations, find your integration, click Delete

Readwise:
  - Go to readwise.io/access_token and regenerate or revoke

GitHub:
  - Go to github.com/settings/tokens and revoke the token

Spotify:
  - Go to spotify.com/account/apps and revoke the app

Pocket:
  - Go to getpocket.com/connected_applications and disconnect

WHOOP:
  - Go to app.whoop.com > Profile > Connected Apps

Twitter/X:
  - Remove TWITTER_USERNAME and TWITTER_PASSWORD from .env
  - Delete ~/.neuron/twscrape_pool.db

Anthropic:
  - Go to console.anthropic.com/settings/keys and revoke the key

---

## What is NOT a concern

- The Chrome extension only sends URLs and page content to localhost:7700.
  It does not transmit data to any external server.
- No analytics, telemetry, or usage tracking is built into Neuron.
- The server has no database of users — it is single-user by design.
- No content is persisted by Anthropic after an API call under standard API
  terms (not Claude.ai consumer product terms).
