import os
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

# Always load from the jimmy project root, regardless of CWD
_ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_FILE, override=True)

# Required
CANVAS_API_TOKEN = os.getenv("CANVAS_API_TOKEN")
CANVAS_API_URL = os.getenv("CANVAS_API_URL")

# Optional integrations — add to .env as needed
NOTION_API_TOKEN = os.getenv("NOTION_API_TOKEN")
READWISE_API_TOKEN = os.getenv("READWISE_API_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Google Suite — one OAuth client covers Calendar, Gmail, Drive across all accounts
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

# Media & social integrations
POCKET_CONSUMER_KEY = os.getenv("POCKET_CONSUMER_KEY")
POCKET_ACCESS_TOKEN = os.getenv("POCKET_ACCESS_TOKEN")
TRAKT_CLIENT_ID = os.getenv("TRAKT_CLIENT_ID")
TRAKT_USERNAME = os.getenv("TRAKT_USERNAME")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
WHOOP_CLIENT_ID = os.getenv("WHOOP_CLIENT_ID")
WHOOP_CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET")

# Twitter/X scraping (optional — needed for live tweet feeds)
TWITTER_USERNAME = os.getenv("TWITTER_USERNAME")
TWITTER_PASSWORD = os.getenv("TWITTER_PASSWORD")
TWITTER_EMAIL    = os.getenv("TWITTER_EMAIL")

# ── LLM provider config (Ollama-first) ────────────────────────────────────────
JIMMY_LLM_PROVIDER = os.getenv("JIMMY_LLM_PROVIDER", "ollama")
JIMMY_MODEL_FAST = os.getenv("JIMMY_MODEL_FAST", "llama3.2:latest")
JIMMY_MODEL_DEFAULT = os.getenv("JIMMY_MODEL_DEFAULT", "llama3.1:8b")
JIMMY_MODEL_DEEP = os.getenv("JIMMY_MODEL_DEEP", "llama3.1:8b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# Tier → model mapping per provider
LLM_TIER_MAP = {
    "ollama": {"fast": JIMMY_MODEL_FAST, "default": JIMMY_MODEL_DEFAULT, "deep": JIMMY_MODEL_DEEP},
    "anthropic": {"fast": "claude-haiku-4-5-20251001", "default": "claude-sonnet-4-6", "deep": "claude-opus-4-6"},
    "openai": {"fast": "gpt-4o-mini", "default": "gpt-4o-mini", "deep": "gpt-4o-mini"},
}

JIMMY_DATA_DIR = Path(os.environ.get("JIMMY_DATA_DIR", str(Path.home() / ".jimmy")))
CHROMA_DIR = JIMMY_DATA_DIR / "chroma"

# Personal milestones
_DATADOG_START_DATE_RAW = os.getenv("DATADOG_START_DATE", "2026-07-06")
try:
    DATADOG_START_DATE = date.fromisoformat(_DATADOG_START_DATE_RAW)
except ValueError:
    DATADOG_START_DATE = date(2026, 7, 6)

# ── User profile (configurable for beta) ─────────────────────────────────────
JIMMY_USER_NAME = os.getenv("JIMMY_USER_NAME", "Ralph")
JIMMY_USER_BIO = os.getenv(
    "JIMMY_USER_BIO",
    "Ralph Betesh. Graduated Columbia University May 2025 with a CS degree. "
    "Starting at Datadog July 2026 as a software engineer. Lives in NYC. "
    "Member of FIJI fraternity at Columbia."
)
JIMMY_USER_CONTEXT = os.getenv(
    "JIMMY_USER_CONTEXT",
    "Sephardic Jewish, observant — keeps Shabbat, Torah study is a core interest. "
    "Past Columbia courses (Operating Systems, Computer Networks, Financial Accounting, "
    "Analysis of Algorithms) are ALL COMPLETED and historical — not current work. "
    "Current interests: AI/ML, startups, Torah, fitness, reading. "
    "Built Jimmy (this app) as a personal project. "
    "Preparing for Datadog by studying observability, distributed systems, and infrastructure."
)

JIMMY_DATA_DIR.mkdir(exist_ok=True)
CHROMA_DIR.mkdir(exist_ok=True)
