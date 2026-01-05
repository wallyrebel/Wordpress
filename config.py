"""
Configuration module for RSS to WordPress automation.
Loads settings from environment variables.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


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
    
    # Required fields
    required_vars = {
        'OPENAI_API_KEY': os.getenv('OPENAI_API_KEY'),
        'WP_URL': os.getenv('WP_URL'),
        'WP_USERNAME': os.getenv('WP_USERNAME'),
        'WP_APP_PASSWORD': os.getenv('WP_APP_PASSWORD'),
        'RSS_FEEDS': os.getenv('RSS_FEEDS'),
    }
    
    # Check for missing required variables
    missing = [key for key, value in required_vars.items() if not value]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    
    # Parse RSS feeds (comma-separated)
    rss_feeds = [url.strip() for url in required_vars['RSS_FEEDS'].split(',') if url.strip()]
    if not rss_feeds:
        raise ValueError("RSS_FEEDS must contain at least one valid URL")
    
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
