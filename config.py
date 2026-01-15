"""
Configuration module for RSS to WordPress automation.
Loads settings from environment variables.
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Path to feeds.txt file (relative to this config file)
FEEDS_FILE_PATH = Path(__file__).parent / "feeds.txt"


@dataclass
class Config:
    """Application configuration."""
    openai_api_key: str
    wp_url: str
    wp_username: str
    wp_app_password: str
    rss_feeds: List[str]
    poll_interval_minutes: int = 30
    image_dir: str = "./images"
    database_path: str = "./processed.db"

    def __post_init__(self):
        # Ensure WordPress URL doesn't have trailing slash
        self.wp_url = self.wp_url.rstrip('/')
        
        # Create image directory if it doesn't exist
        os.makedirs(self.image_dir, exist_ok=True)


def load_config() -> Config:
    """
    Load configuration from environment variables.
    
    Returns:
        Config: Populated configuration object.
        
    Raises:
        ValueError: If required environment variables are missing.
    """
    load_dotenv()
    
    # Required fields (RSS_FEEDS no longer required - we read from feeds.txt)
    required_vars = {
        'OPENAI_API_KEY': os.getenv('OPENAI_API_KEY'),
        'WP_URL': os.getenv('WP_URL'),
        'WP_USERNAME': os.getenv('WP_USERNAME'),
        'WP_APP_PASSWORD': os.getenv('WP_APP_PASSWORD'),
    }
    
    # Check for missing required variables
    missing = [key for key, value in required_vars.items() if not value]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    
    # Load RSS feeds from feeds.txt file (primary) or RSS_FEEDS env var (fallback)
    rss_feeds = []
    if FEEDS_FILE_PATH.exists():
        with open(FEEDS_FILE_PATH, 'r') as f:
            rss_feeds = [line.strip() for line in f if line.strip()]
        logger.info(f"Loaded {len(rss_feeds)} RSS feeds from {FEEDS_FILE_PATH}")
    else:
        # Fallback to environment variable
        rss_feeds_env = os.getenv('RSS_FEEDS', '')
        if rss_feeds_env:
            rss_feeds = [url.strip() for url in rss_feeds_env.split(',') if url.strip()]
            logger.info(f"Loaded {len(rss_feeds)} RSS feeds from RSS_FEEDS environment variable")
    
    if not rss_feeds:
        raise ValueError("No RSS feeds found. Add feeds to feeds.txt or set RSS_FEEDS environment variable.")
    
    # Optional fields with defaults
    poll_interval = int(os.getenv('POLL_INTERVAL_MINUTES', '30'))
    image_dir = os.getenv('IMAGE_DIR', './images')
    database_path = os.getenv('DATABASE_PATH', './processed.db')
    
    config = Config(
        openai_api_key=required_vars['OPENAI_API_KEY'],
        wp_url=required_vars['WP_URL'],
        wp_username=required_vars['WP_USERNAME'],
        wp_app_password=required_vars['WP_APP_PASSWORD'],
        rss_feeds=rss_feeds,
        poll_interval_minutes=poll_interval,
        image_dir=image_dir,
        database_path=database_path,
    )
    
    logger.info(f"Configuration loaded: {len(rss_feeds)} RSS feeds, polling every {poll_interval} minutes")
    return config
