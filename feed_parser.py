"""Fetch metadata first; reject stale entries before permitted full-text reads."""
import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
import feedparser
from bs4 import BeautifulSoup
from ai_rewriter import clean_text
from safe_http import canonical_url, fetch_bytes

logger = logging.getLogger(__name__)

@dataclass
class FeedEntry:
    guid: str
    title: str
    link: str
    published: datetime | None
    summary: str
    content: str
    feed_url: str
    updated: datetime | None = None
    publisher: str = ""

def parsed_date(entry, key):
    value = entry[key + "_parsed"] if key + "_parsed" in entry else None
    if value:
        try:
            return datetime.fromtimestamp(calendar.timegm(value), timezone.utc)
        except (ValueError, OverflowError, TypeError):
            pass
    return None

def _parse_entry(entry, feed_url):
    link = canonical_url(entry.get("link", ""))
    content = (entry.get("content") or [{}])[0].get("value") or entry.get("summary", "")
    return FeedEntry(entry.get("id") or link, clean_text(entry.get("title", "")), link,
        parsed_date(entry, "published"), entry.get("summary", ""), content, feed_url,
        parsed_date(entry, "updated"))

def get_entry_raw(entry):
    return {"media_content": entry.get("media_content"), "media_thumbnail": entry.get("media_thumbnail"),
            "summary": entry.get("summary", ""),
            "content": (entry.get("content") or [{}])[0].get("value", "")}

def fetch_feeds_with_raw(feed_urls, max_entries_per_feed=25, max_age_hours=24, stats=None):
    stats = stats if stats is not None else {}
    results = []
    now = datetime.now(timezone.utc)
    for url in feed_urls:
        detail = stats.setdefault("feeds", {}).setdefault(url, {})
        try:
            data, _, _ = fetch_bytes(url)
            feed = feedparser.parse(data)
            if feed.bozo and not feed.entries:
                raise ValueError("Malformed feed")
            eligible = []
            detail.update(publisher=clean_text(feed.feed.get("title", "")),
                          entries=len(feed.entries), stale=0, undated_or_future=0, invalid=0)
            for raw in feed.entries:
                try:
                    entry = _parse_entry(raw, url)
                    entry.publisher = clean_text(feed.feed.get("title", "")) or urlsplit(entry.link).hostname
                except (ValueError, TypeError, AttributeError):
                    stats["invalid_entries"] = stats.get("invalid_entries", 0) + 1
                    detail["invalid"] += 1
                    continue
                timestamp = entry.updated or entry.published
                if not timestamp or timestamp > now + timedelta(minutes=15):
                    stats["undated_or_future"] = stats.get("undated_or_future", 0) + 1
                    detail["undated_or_future"] += 1
                    continue
                if timestamp < now - timedelta(hours=max_age_hours):
                    detail["stale"] += 1
                    continue
                eligible.append((entry, get_entry_raw(raw)))
            eligible.sort(key=lambda item: item[0].updated or item[0].published)
            # Apply per-feed processing limits after deduplication in main. Otherwise
            # the same old 25 items can permanently hide newer items in busy feeds.
            results.extend(eligible)
            detail.update(status="ok", eligible=len(eligible))
            stats["feeds_ok"] = stats.get("feeds_ok", 0) + 1
        except Exception as exc:
            logger.warning("Feed read failed (%s): %s", type(exc).__name__, urlsplit(url).hostname)
            stats["feeds_failed"] = stats.get("feeds_failed", 0) + 1
            detail.update(status="error", error_type=type(exc).__name__)
    return results

def enrich_entry(entry, policy):
    if len(clean_text(entry.content)) >= 500 or not policy.allow_scrape:
        return entry
    host = urlsplit(entry.link).hostname
    if host not in policy.article_hosts:
        return entry
    data, content_type, final_url = fetch_bytes(entry.link)
    if urlsplit(final_url).hostname not in policy.article_hosts or "html" not in content_type:
        raise ValueError("Article redirected outside permitted hosts or is not HTML")
    soup = BeautifulSoup(data, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "form", "aside"]):
        node.decompose()
    for selector in (".sidearm-story-template-text", ".article-body", ".story-content", "article"):
        node = soup.select_one(selector)
        if node and len(clean_text(str(node))) > len(clean_text(entry.content)):
            entry.content = clean_text(str(node))
            break
    return entry
