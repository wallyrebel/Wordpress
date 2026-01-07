#!/usr/bin/env python3
"""
RSS to WordPress Automation Script.
Monitors RSS feeds and publishes AP-style articles to WordPress.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import Optional, List

from openai import OpenAI

from config import Config, load_config
from database import init_db, is_processed, mark_processed, get_processed_count
from feed_parser import fetch_feeds_with_raw, FeedEntry
from image_handler import get_or_create_image
from ai_rewriter import rewrite_article, RewrittenArticle
from wordpress_api import WordPressAPI
from email_notifier import PublishedArticle, send_github_actions_notification


# Configure logging
def setup_logging(verbose: bool = False):
    """Configure structured logging."""
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('rss_automation.log', encoding='utf-8')
        ]
    )
    
    # Reduce noise from third-party libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('openai').setLevel(logging.WARNING)
    
    return logging.getLogger(__name__)


def process_single_entry(
    entry: FeedEntry,
    raw_entry: dict,
    config: Config,
    openai_client: OpenAI,
    wp_api: WordPressAPI,
    logger: logging.Logger
) -> Optional[int]:
    """
    Process a single feed entry: rewrite, get image, and publish.
    
    Args:
        entry: Parsed feed entry.
        raw_entry: Raw entry data for image extraction.
        config: Application configuration.
        openai_client: Initialized OpenAI client.
        wp_api: WordPress API client.
        logger: Logger instance.
        
    Returns:
        WordPress post ID if successful, None otherwise.
    """
    logger.info(f"Processing entry: {entry.title[:60]}...")
    
    try:
        # Step 1: Rewrite article with AI
        rewritten = rewrite_article(
            title=entry.title,
            content=entry.content,
            link=entry.link,
            openai_client=openai_client
        )
        
        if not rewritten:
            logger.error(f"Failed to rewrite article: {entry.title}")
            return None
        
        logger.info(f"Article rewritten: {rewritten.headline[:50]}...")
        
        # Step 2: Get or generate image
        image_path = get_or_create_image(
            raw_entry=raw_entry,
            title=entry.title,
            content=entry.content,
            openai_client=openai_client,
            image_dir=config.image_dir
        )
        
        if not image_path:
            logger.warning(f"No image available for: {entry.title}")
        
        # Step 3: Get/create category and tags
        category_ids, tag_ids = wp_api.get_category_and_tag_ids(
            category=rewritten.category,
            tags=rewritten.tags
        )
        
        logger.info(f"Categories: {category_ids}, Tags: {tag_ids}")
        
        # Step 4: Upload image to WordPress
        media_id = None
        if image_path:
            media_id = wp_api.upload_media(
                file_path=image_path,
                alt_text=rewritten.headline,
                caption=f"Image for: {rewritten.headline}"
            )
            if media_id:
                logger.info(f"Uploaded media ID: {media_id}")
            else:
                logger.warning("Failed to upload media, continuing without featured image")
        
        # Step 5: Create WordPress post
        post_id = wp_api.create_post(
            title=rewritten.headline,
            content=rewritten.body,
            status="publish",
            category_ids=category_ids,
            tag_ids=tag_ids,
            featured_media_id=media_id
        )
        
        if post_id:
            logger.info(f"Successfully created post ID: {post_id}")
            return post_id
        else:
            logger.error(f"Failed to create post for: {entry.title}")
            return None
            
    except Exception as e:
        logger.exception(f"Error processing entry {entry.title}: {e}")
        return None


def run_feed_processing(config: Config, logger: logging.Logger) -> tuple[int, int, List[PublishedArticle]]:
    """
    Run a single iteration of feed processing.
    
    Args:
        config: Application configuration.
        logger: Logger instance.
        
    Returns:
        Tuple of (processed_count, error_count, published_articles).
    """
    logger.info("=" * 60)
    logger.info(f"Starting feed processing at {datetime.now().isoformat()}")
    logger.info(f"Monitoring {len(config.rss_feeds)} RSS feed(s)")
    
    published_articles: List[PublishedArticle] = []
    
    # Initialize OpenAI client
    openai_client = OpenAI(api_key=config.openai_api_key)
    
    # Initialize WordPress API
    wp_api = WordPressAPI(
        wp_url=config.wp_url,
        username=config.wp_username,
        app_password=config.wp_app_password
    )
    
    # Test WordPress connection
    if not wp_api.test_connection():
        logger.error("Failed to connect to WordPress. Check your credentials.")
        return 0, 1, []
    
    # Fetch all feeds
    # Filter for last 24 hours to prevent backfilling old content
    entries_with_raw = fetch_feeds_with_raw(config.rss_feeds, max_age_hours=24)
    logger.info(f"Fetched {len(entries_with_raw)} total entries from all feeds (last 24h)")
    
    # Filter out already processed entries
    new_entries = []
    for entry, raw in entries_with_raw:
        if not is_processed(entry.guid):
            new_entries.append((entry, raw))
        else:
            logger.debug(f"Skipping already processed: {entry.guid}")
    
    logger.info(f"Found {len(new_entries)} new entries to process")
    
    if not new_entries:
        logger.info("No new entries to process")
        return 0, 0, []
    
    processed_count = 0
    error_count = 0
    
    for entry, raw_entry in new_entries:
        try:
            post_id = process_single_entry(
                entry=entry,
                raw_entry=raw_entry,
                config=config,
                openai_client=openai_client,
                wp_api=wp_api,
                logger=logger
            )
            
            if post_id:
                mark_processed(
                    guid=entry.guid,
                    post_id=post_id,
                    feed_url=entry.feed_url,
                    title=entry.title
                )
                processed_count += 1
                
                # Track for email notification
                wp_post_url = f"{config.wp_url}/?p={post_id}"
                published_articles.append(PublishedArticle(
                    headline=entry.title[:100],
                    source_url=entry.link,
                    wordpress_url=wp_post_url,
                    post_id=post_id
                ))
            else:
                error_count += 1
                
        except Exception as e:
            logger.exception(f"Unexpected error processing {entry.guid}: {e}")
            error_count += 1
        
        # Brief pause between entries to avoid rate limiting
        time.sleep(2)
    
    logger.info(f"Processing complete. Processed: {processed_count}, Errors: {error_count}")
    logger.info(f"Total entries in database: {get_processed_count()}")
    
    return processed_count, error_count, published_articles


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='RSS to WordPress Automation - Monitor RSS feeds and publish AP-style articles'
    )
    parser.add_argument(
        '--schedule',
        action='store_true',
        help='Run continuously with periodic polling (uses POLL_INTERVAL_MINUTES from config)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose/debug logging'
    )
    parser.add_argument(
        '--test-connection',
        action='store_true',
        help='Test WordPress connection and exit'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    logger = setup_logging(verbose=args.verbose)
    
    logger.info("RSS to WordPress Automation starting...")
    
    # Email notification settings from environment
    notify_email = os.getenv('NOTIFY_EMAIL')
    smtp_username = os.getenv('SMTP_USERNAME')
    smtp_password = os.getenv('SMTP_PASSWORD')
    email_enabled = all([notify_email, smtp_username, smtp_password])
    
    if email_enabled:
        logger.info(f"Email notifications enabled. Will notify: {notify_email}")
    else:
        logger.info("Email notifications disabled (missing NOTIFY_EMAIL, SMTP_USERNAME, or SMTP_PASSWORD)")
    
    try:
        # Load configuration
        config = load_config()
        logger.info(f"Configuration loaded. Feeds: {len(config.rss_feeds)} RSS feeds")
        
        # Initialize database
        init_db(config.database_path)
        logger.info(f"Database initialized. Existing entries: {get_processed_count()}")
        
        # Test connection mode
        if args.test_connection:
            wp_api = WordPressAPI(
                wp_url=config.wp_url,
                username=config.wp_username,
                app_password=config.wp_app_password
            )
            if wp_api.test_connection():
                logger.info("WordPress connection test PASSED")
                sys.exit(0)
            else:
                logger.error("WordPress connection test FAILED")
                sys.exit(1)
        
        # Run processing
        if args.schedule:
            logger.info(f"Running in scheduled mode. Polling every {config.poll_interval_minutes} minutes.")
            logger.info("Press Ctrl+C to stop.")
            
            while True:
                try:
                    processed, errors, published = run_feed_processing(config, logger)
                    
                    # Send email notification if articles were published
                    if email_enabled and published:
                        send_github_actions_notification(
                            articles=published,
                            to_email=notify_email,
                            smtp_username=smtp_username,
                            smtp_password=smtp_password
                        )
                    
                    logger.info(f"Sleeping for {config.poll_interval_minutes} minutes...")
                    time.sleep(config.poll_interval_minutes * 60)
                except KeyboardInterrupt:
                    logger.info("Received interrupt signal. Shutting down...")
                    break
        else:
            # Single run
            processed, errors, published = run_feed_processing(config, logger)
            
            # Send email notification if articles were published
            if email_enabled and published:
                send_github_actions_notification(
                    articles=published,
                    to_email=notify_email,
                    smtp_username=smtp_username,
                    smtp_password=smtp_password
                )
            
            if errors > 0 and processed == 0:
                sys.exit(1)
    
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
    
    logger.info("RSS to WordPress Automation finished.")


if __name__ == '__main__':
    main()

