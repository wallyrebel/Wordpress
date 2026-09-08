"""Fetch metadata first; reject stale entries before permitted full-text reads."""
import calendar
import logging
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urljoin
import feedparser
from bs4 import BeautifulSoup
from requests import RequestException
from ai_rewriter import clean_text, InsufficientSource
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


def _read_feed(url):
    try:
        return fetch_bytes(url)[0], None
    except Exception as exc:
        return None, exc

def fetch_feeds_with_raw(feed_urls, max_entries_per_feed=25, max_age_hours=24, stats=None):
    stats = stats if stats is not None else {}
    results = []
    now = datetime.now(timezone.utc)
    # Every configured source is read before any publication/model budget applies.
    # Four bounded reads keep a slow source from delaying the entire expanded list.
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(_read_feed, feed_urls))
        # Retry transport failures after the other feeds have had a turn. A
        # fresh connection can recover an intermittent source timeout without
        # replaying successful sources or hiding an unresolved failure.
        retry_indexes = [i for i, (_, error) in enumerate(fetched)
                         if isinstance(error, (RequestException, TimeoutError))]
        retried = list(pool.map(_read_feed, [feed_urls[i] for i in retry_indexes]))
    for index, result in zip(retry_indexes, retried):
        fetched[index] = result
    if retry_indexes:
        stats['feeds_retried'] = len(retry_indexes)
    retry_urls = {feed_urls[i] for i in retry_indexes}
    for url, (data, error) in zip(feed_urls, fetched):
        detail = stats.setdefault("feeds", {}).setdefault(url, {})
        retried_feed = url in retry_urls
        detail['read_attempts'] = 2 if retried_feed else 1
        try:
            if error:
                raise error
            feed = feedparser.parse(data)
            if not feed.get('version') or (feed.bozo and not feed.entries):
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
            if retried_feed:
                detail['recovered_on_retry'] = True
                stats['feeds_recovered'] = stats.get('feeds_recovered', 0) + 1
        except Exception as exc:
            logger.warning("Feed read failed (%s): %s", type(exc).__name__, urlsplit(url).hostname)
            stats["feeds_failed"] = stats.get("feeds_failed", 0) + 1
            detail.update(status="error", error_type=type(exc).__name__)
    return results

def enrich_entry(entry, policy, raw=None):
    if not policy.allow_scrape:
        return entry
    host = urlsplit(entry.link).hostname
    if host not in policy.article_hosts:
        return entry
    data, content_type, final_url = fetch_bytes(entry.link)
    if urlsplit(final_url).hostname not in policy.article_hosts:
        # Video-only RSS entries can redirect to YouTube. This is a source
        # eligibility hold, not a broken feed or publishing service. Do not
        # broaden the approved hosts or turn video navigation into story facts.
        raise InsufficientSource("Source page redirects outside approved article hosts; no approved article text")
    if "html" not in content_type.lower():
        raise InsufficientSource("Source page is not an HTML article; unsupported document or media")
    soup = BeautifulSoup(data, "html.parser")
    for selector in ("#storyPageContentBody", ".sidearm-story-template-text", ".article-content.redesign-text", ".article-body",
                     ".story-content", ".field--name-body", ".entry-content", "article"):
        node = soup.select_one(selector)
        if node:
            # Older athletics sites wrap the entire story in an ASP.NET form.
            # Select the story before removing embedded navigation/forms.
            for unwanted in node(["script", "style", "nav", "footer", "form", "aside"]):
                unwanted.decompose()
            if len(clean_text(str(node))) > len(clean_text(entry.content)):
                entry.content = clean_text(str(node))
            break
    if raw is not None:
        # Use the source page's own featured image when a feed thumbnail fails.
        # Never harvest related-story thumbnails or guess an unrelated photograph.
        featured_urls = []
        featured = soup.select_one('article img.wp-post-image')
        if featured:
            for candidate in reversed(featured.get('srcset', '').split(',')):
                if candidate.strip():
                    featured_urls.append(urljoin(final_url, candidate.strip().split()[0]))
            for attribute in ('src', 'data-src', 'data-orig-src'):
                if featured.get(attribute):
                    featured_urls.append(urljoin(final_url, featured[attribute]))
        raw['article_images'] = featured_urls + [urljoin(final_url, image['content'])
            for image in soup.select('meta[property="og:image"][content]')[:1]]
    return entry
