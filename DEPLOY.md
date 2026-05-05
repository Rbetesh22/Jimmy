# Deploying Jimmy

Three deployment options, from simplest to most powerful.

---

## Option A: Cloudflare Tunnel (recommended for personal use)

Keeps data on your Mac. Free. Stable URL. No Docker needed.

**How it works:** Your Mac runs the Jimmy server locally. Cloudflare creates an encrypted tunnel from a public URL to `localhost:7700`. Your data never leaves your machine.

### Quick tunnel (random URL, no setup)

```bash
cd ~/neuron
./start.sh
# Prints a random https://xxx.trycloudflare.com URL
# URL changes every time you restart
```

### Named tunnel (stable URL, one-time setup)

Requires a domain on Cloudflare (free plan works).

```bash
# 1. Install cloudflared
brew install cloudflared

# 2. Authenticate
cloudflared login

# 3. Run setup script
./scripts/setup-tunnel.sh jimmy.yourdomain.com

# 4. Start Jimmy + tunnel
source .venv/bin/activate
uvicorn jimmy.api.server:app --port 7700 --host 0.0.0.0 &
cloudflared tunnel run jimmy
```

To auto-start on boot:
```bash
sudo cloudflared service install
```

### Pros/cons
- (+) Free, data stays local, access to Apple Notes / macOS Contacts
- (+) Stable URL with named tunnels
- (-) Mac must be running and connected to the internet
- (-) Restarts require re-launching the tunnel

---

## Option B: Fly.io (recommended for always-on cloud)

Full cloud deploy with persistent storage. Free tier covers small apps.

### One-time setup
```bash
# 1. Install flyctl
brew install flyctl

# 2. Login / create account
fly auth login

# 3. Create the app + persistent volume
fly apps create jimmy-rb
fly volumes create jimmy_data --app jimmy-rb --region ewr --size 20
```

### Set secrets (API keys)
```bash
fly secrets set --app jimmy-rb \
  ANTHROPIC_API_KEY=xxx \
  OPENAI_API_KEY=yyy \
  READWISE_API_TOKEN=zzz \
  GOOGLE_CLIENT_ID=xxx \
  GOOGLE_CLIENT_SECRET=yyy
# See .env.example for full list
```

### Seed your ChromaDB data (first deploy)
```bash
# Copy local ChromaDB to the volume
fly ssh console --app jimmy-rb -C "mkdir -p /data/chroma"
tar czf - ~/.jimmy/chroma | fly ssh console --app jimmy-rb -C "tar xzf - -C /data"

# Copy accounts.db and OAuth tokens
fly sftp shell --app jimmy-rb
> put ~/.jimmy/google_token_*.json /data/
> put ~/neuron/accounts.db /data/
```

### Deploy
```bash
cd ~/neuron
./deploy-fly.sh      # wraps: fly deploy --remote-only

# Or manually:
fly deploy --app jimmy-rb --remote-only
```

### Live URL
`https://jimmy-rb.fly.dev`

### Useful commands
```bash
fly logs --app jimmy-rb          # tail logs
fly ssh console --app jimmy-rb   # shell into running instance
fly scale memory 4096            # bump RAM if needed
fly status --app jimmy-rb        # health
```

### Pros/cons
- (+) Always on, no Mac dependency
- (+) Global edge network, fast from anywhere
- (-) ChromaDB data must be uploaded (~2GB)
- (-) No access to Apple Notes or macOS Contacts (cloud-only data sources)
- (-) Free tier may not cover 4GB RAM; expect ~$10-15/month

---

## Option C: GCP Cloud Run

Requires billing enabled on your GCP project.

Current project: `persuasive-net-439300-d5`

```bash
# Enable APIs
gcloud services enable run.googleapis.com containerregistry.googleapis.com storage.googleapis.com

# Create GCS bucket for data persistence
gcloud storage buckets create gs://persuasive-net-439300-d5-jimmy-data --location=us-east1

# Grant default SA access
SA="$(gcloud projects describe persuasive-net-439300-d5 --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
gcloud storage buckets add-iam-policy-binding gs://persuasive-net-439300-d5-jimmy-data \
  --member="serviceAccount:${SA}" --role="roles/storage.objectAdmin"

# Deploy
./deploy.sh
```

---

## Local Docker (testing)

```bash
cp .env.example .env.local   # fill in keys
docker compose up
# Visit http://localhost:7700
```

With Cloudflare tunnel via Docker:
```bash
# Add CLOUDFLARE_TUNNEL_TOKEN to .env.local first
docker compose --profile tunnel up -d
```

---

## Data persistence

| Path | Contents |
|------|----------|
| `$JIMMY_DATA_DIR/chroma/` | ChromaDB vector store (152K+ chunks) |
| `$JIMMY_DATA_DIR/*.json` | AI cache files (digest, news, graph, etc.) |
| `$JIMMY_DATA_DIR/google_token_*.json` | Google OAuth tokens |
| `$JIMMY_DATA_DIR/whoop_token.json` | Whoop OAuth token |
| `$JIMMY_DATA_DIR/spotify_token.json` | Spotify OAuth token |

`JIMMY_DATA_DIR` defaults to `~/.jimmy` locally, `/data` in Docker/Fly/Cloud Run.

## Feature availability by deployment mode

| Feature | Tunnel | Fly.io | Cloud Run |
|---------|--------|--------|-----------|
| Ask / search | Yes | Yes | Yes |
| Network / CRM | Yes | Yes | Yes |
| Daily digest | Yes | Yes | Yes |
| Google Calendar/Gmail/Drive | Yes | Yes | Yes |
| Apple Notes ingestion | Yes | No | No |
| macOS Contacts sync | Yes | No | No |
| Readwise / Notion / GitHub | Yes | Yes | Yes |
| Always-on (no Mac needed) | No | Yes | Yes |
