"""
RSS Feed Parser module.
Fetches and parses RSS feeds, extracting relevant entry data.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
import feedparser
from time import mktime

logger = logging.getLogger(__name__)


@dataclass
class FeedEntry:
    """Represents a parsed RSS feed entry."""
    guid: str
    title: str
    link: str
    published: Optional[datetime]
    summary: str
    content: str
    feed_url: str


def parse_feed(feed_url: str) -> List[FeedEntry]:
    """
    Fetch and parse an RSS feed.
    
    Args:
        feed_url: URL of the RSS feed to parse.
        
    Returns:
        List of FeedEntry objects.
    """
    logger.info(f"Fetching feed: {feed_url}")
    
    try:
        feed = feedparser.parse(feed_url)
        
        if feed.bozo and feed.bozo_exception:
            logger.warning(f"Feed parsing issue for {feed_url}: {feed.bozo_exception}")
        
        if not feed.entries:
            logger.info(f"No entries found in feed: {feed_url}")
            return []
        
        entries = []
        for entry in feed.entries:
            parsed_entry = _parse_entry(entry, feed_url)
            if parsed_entry:
                entries.append(parsed_entry)
        
        logger.info(f"Parsed {len(entries)} entries from {feed_url}")
        return entries
        
    except Exception as e:
        logger.error(f"Failed to parse feed {feed_url}: {e}")
        return []


def _parse_entry(entry, feed_url: str) -> Optional[FeedEntry]:
    """
    Parse a single feed entry.
    
    Args:
        entry: A feedparser entry object.
        feed_url: URL of the source feed.
        
    Returns:
        FeedEntry object or None if parsing fails.
    """
    try:
        # Get GUID (fall back to link if not present)
        guid = getattr(entry, 'id', None) or getattr(entry, 'link', None)
        if not guid:
            logger.warning("Entry has no GUID or link, skipping")
            return None
        
        # Get title
        title = getattr(entry, 'title', 'Untitled')
        
        # Get link
        link = getattr(entry, 'link', '')
        
        # Get published date
        published = None
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            try:
                published = datetime.fromtimestamp(mktime(entry.published_parsed))
            except (TypeError, ValueError):
                pass
        elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            try:
                published = datetime.fromtimestamp(mktime(entry.updated_parsed))
            except (TypeError, ValueError):
                pass
        
        # Get summary
        summary = getattr(entry, 'summary', '')
        
        # Get full content (prefer content over summary)
        content = ''
        if hasattr(entry, 'content') and entry.content:
            content = entry.content[0].get('value', '')
        if not content:
            content = summary
        
        return FeedEntry(
            guid=guid,
            title=title,
            link=link,
            published=published,
            summary=summary,
            content=content,
            feed_url=feed_url
        )
        
    except Exception as e:
        logger.error(f"Failed to parse entry: {e}")
        return None


def get_entry_raw(entry) -> dict:
    """
    Get raw entry data for image extraction.
    This preserves the original feedparser entry attributes.
    
    Args:
        entry: A feedparser entry object.
        
    Returns:
        Dictionary with raw entry attributes.
    """
    return {
        'media_content': getattr(entry, 'media_content', None),
        'media_thumbnail': getattr(entry, 'media_thumbnail', None),
        'summary': getattr(entry, 'summary', ''),
        'content': entry.content[0].get('value', '') if hasattr(entry, 'content') and entry.content else '',
    }


def fetch_feeds_with_raw(feed_urls: List[str], max_entries_per_feed: int = 5) -> List[tuple]:
    """
    Fetch feeds and return both parsed entries and raw data for image extraction.
    
    Args:
        feed_urls: List of RSS feed URLs.
        max_entries_per_feed: Maximum number of entries to fetch per feed (default: 5).
        
    Returns:
        List of tuples (FeedEntry, raw_entry_dict).
    """
    results = []
    
    for feed_url in feed_urls:
        logger.info(f"Fetching feed: {feed_url}")
        
        try:
            feed = feedparser.parse(feed_url)
            
            if feed.bozo and feed.bozo_exception:
                logger.warning(f"Feed parsing issue for {feed_url}: {feed.bozo_exception}")
            
            # Limit to max_entries_per_feed most recent entries
            entries_to_process = feed.entries[:max_entries_per_feed]
            feed_entry_count = 0
            
            for entry in entries_to_process:
                parsed_entry = _parse_entry(entry, feed_url)
                if parsed_entry:
                    raw_data = get_entry_raw(entry)
                    results.append((parsed_entry, raw_data))
                    feed_entry_count += 1
            
            logger.info(f"Fetched {feed_entry_count} entries from {feed_url} (limited to {max_entries_per_feed})")
                    
        except Exception as e:
            logger.error(f"Failed to fetch feed {feed_url}: {e}")
            continue
    
    logger.info(f"Total entries fetched: {len(results)}")
    return results
