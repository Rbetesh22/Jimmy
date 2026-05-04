# Deploying Jimmy

## Option A: Fly.io (recommended — fastest, free tier)

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
  READWISE_API_TOKEN=zzz
# See .env.example for full list
```

### Seed your ChromaDB data (first deploy)
```bash
# Copy local ChromaDB to the volume via a temporary machine
fly ssh console --app jimmy-rb -C "mkdir -p /data/chroma"
# From local:
tar czf - ~/.jimmy/chroma | fly ssh console --app jimmy-rb -C "tar xzf - -C /data"
# Copy OAuth tokens too:
fly ssh console --app jimmy-rb -C "ls /data"
```

### Deploy
```bash
cd ~/jimmy
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
fly scale memory 4096             # bump RAM if needed
fly status --app jimmy-rb        # health
```

---

## Option B: GCP Cloud Run

**Requires billing enabled on your GCP project.**

Current project: `persuasive-net-439300-d5` (no billing yet).

To enable billing: https://console.cloud.google.com/billing

Once billing is enabled:
```bash
# Enable APIs
gcloud services enable run.googleapis.com containerregistry.googleapis.com storage.googleapis.com

# Create GCS bucket for data persistence
gcloud storage buckets create gs://persuasive-net-439300-d5-jimmy-data --location=us-east1

# Grant default SA access
SA="423140233555-compute@developer.gserviceaccount.com"
gcloud storage buckets add-iam-policy-binding gs://persuasive-net-439300-d5-jimmy-data \
  --member="serviceAccount:${SA}" --role="roles/storage.objectAdmin"

# Deploy
./deploy.sh
```

---

## Local Docker test
```bash
cp .env.example .env.local   # fill in keys
docker-compose up
# Visit http://localhost:7700
```

## Data persistence

| Path | Contents |
|------|----------|
| `$JIMMY_DATA_DIR/chroma/` | ChromaDB vector store (141k+ chunks) |
| `$JIMMY_DATA_DIR/*.json` | AI cache files (digest, news, graph, etc.) |
| `$JIMMY_DATA_DIR/google_token_*.json` | Google OAuth tokens |
| `$JIMMY_DATA_DIR/whoop_token.json` | Whoop OAuth token |
| `$JIMMY_DATA_DIR/spotify_token.json` | Spotify OAuth token |

`JIMMY_DATA_DIR` defaults to `~/.jimmy` locally, `/data` in Docker/Fly/Cloud Run.
