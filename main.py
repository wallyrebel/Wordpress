#!/usr/bin/env python3
"""Nano extraction -> Luna drafting -> Nano verification -> WordPress receipt."""
import argparse
import hashlib
import html
import json
import logging
import os
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from openai import OpenAI
from ai_rewriter import rewrite_article, fingerprint, clean_text, InsufficientSource, ModelOutputError, PROMPT_VERSION
from config import load_config
from database import Store
from feed_parser import fetch_feeds_with_raw, enrich_entry
from image_handler import get_source_image
from wordpress_api import WordPressAPI

logger = logging.getLogger(__name__)

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)

def source_key(entry):
    return hashlib.sha256(entry.link.encode()).hexdigest()

def publication_status(article, policy, config, category_ids):
    if (config.publish_mode == "auto" and policy.auto_publish and policy.reuse_allowed
            and policy.publisher and not article.requires_review and category_ids):
        return "publish"
    return "hold"

def run_feed_processing(config, dry_run=False, limit=None, client=None, wp=None):
    stats = {"created": 0, "previews": 0, "held": 0, "skipped": 0, "errors": 0, "duplicates": 0}
    store = Store(config.database_path) if not dry_run else None
    client = client or OpenAI(api_key=config.openai_api_key, timeout=90, max_retries=2)
    wp = wp or (None if dry_run else WordPressAPI(config.wp_url, config.wp_username, config.wp_app_password))
    try:
        if wp:
            wp.test_connection()  # Fail before model spending if companion is missing.
        entries = fetch_feeds_with_raw(config.rss_feeds, config.max_entries_per_feed,
                                       config.max_age_hours, stats)
        seen = set()
        attempted = 0
        feed_attempts = {}
        for entry, raw in entries:
            key = source_key(entry)
            if key in seen:
                continue
            seen.add(key)
            policy = config.policy(entry.feed_url)
            policy = replace(policy, publisher=policy.publisher or entry.publisher,
                image_credit=policy.image_credit or ("Source: " + (policy.publisher or entry.publisher)))
            original_hash = fingerprint(entry.title, entry.content + json.dumps(raw, sort_keys=True, default=str)
                + PROMPT_VERSION + config.extraction_model + config.drafting_model
                + json.dumps(asdict(policy), sort_keys=True) + json.dumps(config.category_ids, sort_keys=True))
            prior = {}
            try:
                cached = store.get(key) if store else None
                if cached and cached.get("feed_hash") == original_hash:
                    stats["duplicates"] += 1
                    continue
                prior = wp.receipt(key) if wp else {}
                # Legacy receipt is adopted server-side, without republishing old stories.
                legacy_id = store.legacy_post(entry.guid) if store and not prior.get("post_id") else None
                if legacy_id:
                    receipt = wp.upsert({"source_key": key, "content_hash": original_hash,
                        "source_url": entry.link, "adopt_post_id": legacy_id})
                    store.save(key, original_hash, {**receipt, "feed_hash": original_hash})
                    stats["duplicates"] += 1
                    continue
                # Unknown reuse permissions create a local editorial queue, no model spend.
                if not policy.reuse_allowed:
                    write_json(Path(config.review_dir) / (key + ".json"),
                        {"status": "source_policy_needed", "source_url": entry.link,
                         "feed_url": entry.feed_url, "title": entry.title})
                    stats["skipped"] += 1
                    continue
                if attempted >= (limit or config.max_posts_per_run):
                    break
                if feed_attempts.get(entry.feed_url, 0) >= config.max_entries_per_feed:
                    continue
                attempted += 1
                feed_attempts[entry.feed_url] = feed_attempts.get(entry.feed_url, 0) + 1
                entry = enrich_entry(entry, policy)
                digest = fingerprint(entry.title, entry.content)
                if digest in prior.get("versions", {}):
                    if store:
                        store.save(key, digest, {**prior["versions"][digest], "feed_hash": original_hash})
                    stats["duplicates"] += 1
                    continue
                if prior.get("post_id"):
                    stats["held"] += 1
                    write_json(Path(config.review_dir) / (key + ".json"),
                        {"status": "source_update", "source_url": entry.link,
                         "original_post_id": prior["post_id"], "source_text": clean_text(entry.content)})
                    continue
                image = get_source_image(raw, policy, config.image_dir)
                if not image:
                    raise InsufficientSource("No eligible featured image (minimum 1200px wide)")
                article = rewrite_article(entry.title, entry.content, entry.link, client,
                    extraction_model=config.extraction_model, drafting_model=config.drafting_model,
                    publisher=policy.publisher,
                    source_date=(entry.published or entry.updated).isoformat())
                record = {"status": "preview", "source_url": entry.link, "feed_url": entry.feed_url,
                          "content_hash": digest, "source_text": clean_text(entry.content),
                          "article": asdict(article)}
                write_json(Path(config.review_dir) / (key + ".json"), record)
                if dry_run:
                    stats["previews"] += 1
                    continue
                if article.requires_review or not policy.auto_publish:
                    record["status"] = "held"
                    record["reasons"] = article.review_reasons or ["Source not enabled for automatic publishing"]
                    write_json(Path(config.review_dir) / (key + ".json"), record)
                    if store:
                        store.save(key, digest, {"status":"held", "feed_hash":original_hash})
                    stats["held"] += 1
                    continue
                category_ids, tag_ids = wp.taxonomy(article.category, article.tags, config.category_ids)
                status = publication_status(article, policy, config, category_ids)
                if status != "publish" or not tag_ids:
                    record["status"] = "held"
                    record["reasons"] = article.review_reasons + ([] if category_ids else ["Category mapping needed"]) + ([] if tag_ids else ["No supported tags"])
                    write_json(Path(config.review_dir) / (key + ".json"), record)
                    if store:
                        store.save(key, digest, {"status":"held", "feed_hash":original_hash})
                    stats["held"] += 1
                    continue
                media_id = wp.upload_media(image, article.headline)
                if not media_id:
                    raise ValueError("Featured image upload failed")
                publisher = policy.publisher or "original source"
                source_line = '<p class="news-source">Source: <a href="' + html.escape(
                    entry.link, quote=True) + '">' + html.escape(publisher) + "</a>.</p>"
                receipt = wp.upsert({
                    "source_key": key, "content_hash": digest, "source_url": entry.link,
                    "title": article.headline, "content": article.body + source_line,
                    "excerpt": article.excerpt, "status": status,
                    "categories": category_ids, "tags": tag_ids, "featured_media": media_id,
                    "review_reasons": article.review_reasons + ([] if category_ids else ["Category mapping needed"]),
                    "source_published": (entry.published or entry.updated).isoformat(),
                    "evidence": article.evidence})
                store.save(key, digest, {**receipt, "feed_hash": original_hash})
                record["status"], record["receipt"] = receipt["status"], receipt
                write_json(Path(config.review_dir) / (key + ".json"), record)
                stats["created"] += 1
            except InsufficientSource as exc:
                stats["skipped"] += 1
                write_json(Path(config.review_dir) / (key + ".json"),
                    {"status": "insufficient_source", "source_url": entry.link, "reason": str(exc)})
                # Rejections can be reconsidered when the source, image or prompt changes.
                if store:
                    store.save(key, original_hash, {"status":"held", "feed_hash":original_hash})
            except ModelOutputError as exc:
                stats["held"] += 1
                write_json(Path(config.review_dir) / (key + ".json"),
                    {"status":"validation_failed", "source_url":entry.link, "reason":str(exc)})
                if store:
                    store.save(key, original_hash, {"status":"held", "feed_hash":original_hash})
            except Exception as exc:
                stats["errors"] += 1
                # Never include HTTP request objects/headers or keys in logs.
                logger.error("Item held; source=%s error=%s", key[:12], type(exc).__name__)
                write_json(Path(config.review_dir) / (key + ".json"),
                    {"status": "error", "source_url": entry.link, "error_type": type(exc).__name__})
        if stats.get("feeds_failed"):
            stats["errors"] += stats["feeds_failed"]
        return stats
    finally:
        if store:
            store.close()
        write_json(Path(config.review_dir) / "run-summary.json", stats)
        logger.info("Run summary: %s", json.dumps(stats))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Model calls and local previews only; no WP/DB writes")
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--test-connection", action="store_true")
    args = parser.parse_args()
    if args.max_items is not None and args.max_items < 1:
        parser.error("--max-items must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler("rss_automation.log", encoding="utf-8")])
    for name in ("openai", "httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        config = load_config(require_wp=not args.dry_run)
        if args.test_connection:
            WordPressAPI(config.wp_url, config.wp_username, config.wp_app_password).test_connection()
            return 0
        while True:
            stats = run_feed_processing(config, args.dry_run, args.max_items)
            if not args.schedule:
                return 1 if stats["errors"] else 0
            time.sleep(config.poll_interval_minutes * 60)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error("Run stopped: %s", type(exc).__name__)
        return 1

if __name__ == "__main__":
    sys.exit(main())
