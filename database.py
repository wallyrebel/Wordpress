"""
Database module for tracking processed RSS feed entries.
Uses SQLite for persistent storage.
"""

import sqlite3
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_db_path: str = "./processed.db"


def init_db(db_path: str = "./processed.db") -> None:
    """
    Initialize the SQLite database and create tables if they don't exist.
    
    Args:
        db_path: Path to the SQLite database file.
    """
    global _db_path
    _db_path = db_path
    
    conn = sqlite3.connect(_db_path)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT UNIQUE NOT NULL,
            post_id INTEGER,
            feed_url TEXT,
            title TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create index for faster lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_guid ON processed_entries(guid)
    ''')
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {_db_path}")


def is_processed(guid: str) -> bool:
    """
    Check if a feed entry has already been processed.
    
    Args:
        guid: The unique identifier of the feed entry.
        
    Returns:
        True if the entry has been processed, False otherwise.
    """
    conn = sqlite3.connect(_db_path)
    cursor = conn.cursor()
    
    cursor.execute('SELECT 1 FROM processed_entries WHERE guid = ?', (guid,))
    result = cursor.fetchone()
    
    conn.close()
    return result is not None


def mark_processed(guid: str, post_id: int, feed_url: str = None, title: str = None) -> None:
    """
    Mark a feed entry as processed.
    
    Args:
        guid: The unique identifier of the feed entry.
        post_id: The WordPress post ID created for this entry.
        feed_url: Optional URL of the source feed.
        title: Optional title of the entry.
    """
    conn = sqlite3.connect(_db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO processed_entries (guid, post_id, feed_url, title, processed_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (guid, post_id, feed_url, title, datetime.utcnow()))
        
        conn.commit()
        logger.info(f"Marked entry as processed: {guid} -> Post ID {post_id}")
    except sqlite3.IntegrityError:
        logger.warning(f"Entry already exists in database: {guid}")
    finally:
        conn.close()


def get_processed_count() -> int:
    """
    Get the total number of processed entries.
    
    Returns:
        Number of processed entries in the database.
    """
    conn = sqlite3.connect(_db_path)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM processed_entries')
    count = cursor.fetchone()[0]
    
    conn.close()
    return count


def get_post_id_for_guid(guid: str) -> Optional[int]:
    """
    Get the WordPress post ID for a previously processed GUID.
    
    Args:
        guid: The unique identifier of the feed entry.
        
    Returns:
        The post ID if found, None otherwise.
    """
    conn = sqlite3.connect(_db_path)
    cursor = conn.cursor()
    
    cursor.execute('SELECT post_id FROM processed_entries WHERE guid = ?', (guid,))
    result = cursor.fetchone()
    
    conn.close()
    return result[0] if result else None
