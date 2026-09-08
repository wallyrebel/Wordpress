"""Explicit source policies and conservative publication defaults."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv
from safe_http import canonical_url

ROOT = Path(__file__).resolve().parent
FEEDS_FILE_PATH = ROOT / "feeds.txt"

@dataclass
class SourcePolicy:
    publisher: str = ""
    reuse_allowed: bool = True
    auto_publish: bool = True
    allow_scrape: bool = False
    article_hosts: list[str] = field(default_factory=list)
    image_reuse_allowed: bool = True
    image_credit: str = ""

@dataclass
class Config:
    openai_api_key: str
    wp_url: str
    wp_username: str
    wp_app_password: str
    rss_feeds: list[str]
    poll_interval_minutes: int = 15
    image_dir: str = "./images"
    database_path: str = "./processed.db"
    extraction_model: str = "gpt-5-nano"
    drafting_model: str = "gpt-5.6-luna"
    publish_mode: str = "auto"
    max_posts_per_run: int = 30
    max_run_seconds: int = 600
    max_entries_per_feed: int = 25
    max_age_hours: int = 24
    review_dir: str = "./review"
    sources: dict[str, SourcePolicy] = field(default_factory=dict)
    category_ids: dict[str, int] = field(default_factory=dict)

    def policy(self, feed):
        return self.sources.get(feed, SourcePolicy())

def load_config(require_wp=True):
    load_dotenv(ROOT / ".env")
    required = ["OPENAI_API_KEY"] + (["WP_URL", "WP_USERNAME", "WP_APP_PASSWORD"] if require_wp else [])
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    # Keep feeds.txt as primary to preserve existing installations.
    path = Path(os.getenv("FEEDS_FILE", str(FEEDS_FILE_PATH)))
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else os.getenv("RSS_FEEDS", "").split(",")
    feeds = list(dict.fromkeys(canonical_url(line.strip().lstrip("\ufeff"))
        for line in lines if line.strip() and not line.lstrip().startswith("#")))
    if not feeds:
        raise ValueError("No feeds configured")
    source_file = Path(os.getenv("SOURCES_FILE", str(ROOT / "sources.json")))
    raw = json.loads(source_file.read_text(encoding="utf-8-sig")) if source_file.exists() else {}
    sources = {}
    for url, value in raw.items():
        policy = SourcePolicy(**value)
        for flag in ("reuse_allowed", "auto_publish", "allow_scrape", "image_reuse_allowed"):
            if not isinstance(getattr(policy, flag), bool):
                raise ValueError("Source flags must be JSON booleans")
        if policy.auto_publish and not policy.reuse_allowed:
            raise ValueError("Disabled sources must set auto_publish=false")
        sources[canonical_url(url)] = policy
    mode = os.getenv("PUBLISH_MODE", "auto")
    if mode != "auto":
        raise ValueError("PUBLISH_MODE must be auto; use --dry-run for local previews")
    wp_url = os.getenv("WP_URL", "").rstrip("/")
    if require_wp and (urlsplit(wp_url).scheme != "https" or urlsplit(wp_url).username):
        raise ValueError("WordPress credentials require HTTPS")
    category_file = ROOT / "category-map.json"
    categories = json.loads(category_file.read_text(encoding="utf-8")) if category_file.exists() else {}
    categories.update(json.loads(os.getenv("CATEGORY_IDS_JSON", "{}")))
    from ai_rewriter import CATEGORIES
    if any(k not in CATEGORIES or type(v) is not int or v <= 0 for k, v in categories.items()):
        raise ValueError("Category map needs permitted names and positive IDs")
    def positive(name, default):
        value = int(os.getenv(name, str(default)))
        if value <= 0:
            raise ValueError(name + " must be positive")
        return value
    return Config(os.getenv("OPENAI_API_KEY", ""), wp_url, os.getenv("WP_USERNAME", ""),
        os.getenv("WP_APP_PASSWORD", ""), feeds,
        poll_interval_minutes=positive("POLL_INTERVAL_MINUTES", 15),
        image_dir=os.getenv("IMAGE_DIR", "./images"), database_path=os.getenv("DATABASE_PATH", "./processed.db"),
        extraction_model=os.getenv("EXTRACTION_MODEL", "gpt-5-nano"),
        drafting_model=os.getenv("DRAFTING_MODEL", "gpt-5.6-luna"), publish_mode=mode,
        max_posts_per_run=positive("MAX_POSTS_PER_RUN", 30),
        max_run_seconds=positive("MAX_RUN_SECONDS", 600),
        max_entries_per_feed=positive("MAX_ENTRIES_PER_FEED", 25),
        max_age_hours=positive("MAX_AGE_HOURS", 24), sources=sources, category_ids=categories,
        review_dir=os.getenv("REVIEW_DIR", "./review"))
