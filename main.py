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
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
from ai_rewriter import rewrite_article, fingerprint, clean_text, InsufficientSource, ModelOutputError, PROMPT_VERSION
from config import load_config
from database import Store
from feed_parser import fetch_feeds_with_raw, enrich_entry
from image_handler import get_source_image, IMAGE_POLICY_VERSION
from wordpress_api import WordPressAPI

logger = logging.getLogger(__name__)
MAX_ITEM_MODEL_ATTEMPTS = 2
MAX_IMAGE_ATTEMPTS = 3
IMAGE_RETRY_SECONDS = 1800

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
    stats = {"created": 0, "previews": 0, "held": 0, "skipped": 0, "errors": 0,
             "duplicates": 0, "source_updates": 0, "model_attempts": 0,
             "reasons": {}}
    store = Store(config.database_path) if not dry_run else None
    client = client or OpenAI(api_key=config.openai_api_key, timeout=90, max_retries=2)
    wp = wp or (None if dry_run else WordPressAPI(config.wp_url, config.wp_username, config.wp_app_password))
    outcomes = []
    def observe(entry, status, reason="", cached=False):
        item = {"source_url": entry.link, "feed_url": entry.feed_url, "title": entry.title,
                "publisher": entry.publisher, "status": status, "reason": reason, "cached": cached}
        outcomes.append(item)
        detail = stats.setdefault("feeds", {}).setdefault(entry.feed_url, {})
        counts = detail.setdefault("outcomes", {})
        counts[status] = counts.get(status, 0) + 1
        if status not in ("publish", "preview", "duplicate"):
            stats["attention_required"] = stats.get("attention_required", 0) + 1
            # Persist a readable record even when the run restored only the DB cache.
            if cached:
                write_json(Path(config.review_dir) / (source_key(entry) + ".json"), item)
    try:
        if wp:
            wp.test_connection()  # Fail before model spending if companion is missing.
        entries = fetch_feeds_with_raw(config.rss_feeds, config.max_entries_per_feed,
                                       config.max_age_hours, stats)
        # A fixed feed-list order let earlier busy sources starve later sources.
        entries.sort(key=lambda item: item[0].updated or item[0].published
                     or datetime.max.replace(tzinfo=timezone.utc))
        seen = set()
        attempted = 0
        feed_attempts = {}
        for entry, raw in entries:
            key = source_key(entry)
            if key in seen:
                observe(entry, "duplicate", "Repeated source URL in this run")
                continue
            seen.add(key)
            policy = config.policy(entry.feed_url)
            policy = replace(policy, publisher=policy.publisher or entry.publisher,
                image_credit=policy.image_credit or ("Source: " + (policy.publisher or entry.publisher)))
            original_hash = fingerprint(entry.title, entry.content + json.dumps(raw, sort_keys=True, default=str)
                + PROMPT_VERSION + IMAGE_POLICY_VERSION + config.extraction_model + config.drafting_model
                + json.dumps(asdict(policy), sort_keys=True) + json.dumps(config.category_ids, sort_keys=True))
            prior = {}
            item_attempts = 0
            image_attempts = 0
            correction_feedback = ""
            try:
                cached = store.get(key) if store else None
                if cached and cached.get("feed_hash") == original_hash:
                    item_attempts = cached.get("model_attempts", 0)
                    image_attempts = cached.get("image_attempts", 0)
                    correction_feedback = cached.get("reason", "")
                    retry_model = cached.get("status") == "retry_pending" and item_attempts < MAX_ITEM_MODEL_ATTEMPTS
                    retry_image = (cached.get("status") == "image_retry_pending"
                                   and image_attempts < MAX_IMAGE_ATTEMPTS
                                   and time.time() >= cached.get("retry_after", 0))
                    if not retry_model and not retry_image:
                        stats["duplicates"] += 1
                        if cached.get("status") in ("held", "source_update", "image_retry_pending", "retry_pending"):
                            stats["cached_holds"] = stats.get("cached_holds", 0) + 1
                            observe(entry, cached["status"], correction_feedback or "Previously held; source or configuration must change", True)
                        else:
                            observe(entry, "duplicate", "Already processed")
                        continue
                prior = wp.receipt(key) if wp else {}
                # Legacy receipt is adopted server-side, without republishing old stories.
                legacy_id = store.legacy_post(entry.guid) if store and not prior.get("post_id") else None
                if legacy_id:
                    entry = enrich_entry(entry, policy)
                    digest = fingerprint(entry.title, entry.content)
                    receipt = wp.upsert({"source_key": key, "content_hash": digest,
                        "source_url": entry.link, "adopt_post_id": legacy_id})
                    store.save(key, digest, {**receipt, "feed_hash": original_hash})
                    stats["duplicates"] += 1
                    observe(entry, "duplicate", "Adopted existing publication")
                    continue
                # Unknown reuse permissions create a local editorial queue, no model spend.
                if not policy.reuse_allowed:
                    write_json(Path(config.review_dir) / (key + ".json"),
                        {"status": "source_policy_needed", "source_url": entry.link,
                         "feed_url": entry.feed_url, "title": entry.title})
                    stats["skipped"] += 1
                    observe(entry, "held", "Source reuse is disabled")
                    continue
                entry = enrich_entry(entry, policy)
                digest = fingerprint(entry.title, entry.content)
                if digest in prior.get("versions", {}):
                    if store:
                        store.save(key, digest, {**prior["versions"][digest], "feed_hash": original_hash})
                    stats["duplicates"] += 1
                    observe(entry, "duplicate", "WordPress receipt confirms this source version")
                    continue
                if prior.get("post_id"):
                    stats["held"] += 1
                    stats["source_updates"] += 1
                    write_json(Path(config.review_dir) / (key + ".json"),
                        {"status": "source_update", "source_url": entry.link,
                         "original_post_id": prior["post_id"], "source_text": clean_text(entry.content)})
                    # Previously these same updates consumed every run's budget and
                    # were never cached, permanently starving unpublished stories.
                    if store:
                        store.save(key, digest, {"status": "source_update",
                            "post_id": prior["post_id"], "feed_hash": original_hash,
                            "reason": "Published source changed; editorial update required"})
                    observe(entry, "source_update", "Published source changed; editorial update required")
                    continue
                if attempted >= (limit or config.max_posts_per_run):
                    stats["budget_reached"] = True
                    stats["deferred"] = stats.get("deferred", 0) + 1
                    observe(entry, "deferred", "Run model-attempt budget reached; retry next run")
                    continue
                if feed_attempts.get(entry.feed_url, 0) >= config.max_entries_per_feed:
                    stats["deferred"] = stats.get("deferred", 0) + 1
                    observe(entry, "deferred", "Per-feed model-attempt budget reached; retry next run")
                    continue
                image_attempts += 1
                image = get_source_image(raw, policy, config.image_dir)
                if not image:
                    raise InsufficientSource("No eligible featured image (minimum 600px wide and 400px high)")
                # Limit paid model attempts, not deduplication, source updates or
                # missing-image checks. All publication checks still apply.
                while True:
                    attempted += 1
                    item_attempts += 1
                    stats["model_attempts"] = attempted
                    feed_attempts[entry.feed_url] = feed_attempts.get(entry.feed_url, 0) + 1
                    try:
                        article = rewrite_article(entry.title, entry.content, entry.link, client,
                            extraction_model=config.extraction_model, drafting_model=config.drafting_model,
                            publisher=policy.publisher,
                            source_date=(entry.published or entry.updated).isoformat(),
                            approved_primary_source=policy.reuse_allowed and policy.auto_publish,
                            correction_feedback=correction_feedback)
                        break
                    except (ModelOutputError, InsufficientSource) as exc:
                        repairable = isinstance(exc, ModelOutputError) or str(exc) == "Missing central facts"
                        if (not repairable or item_attempts >= MAX_ITEM_MODEL_ATTEMPTS
                                or attempted >= (limit or config.max_posts_per_run)
                                or feed_attempts[entry.feed_url] >= config.max_entries_per_feed):
                            raise
                        correction_feedback = str(exc)
                        stats["repair_attempts"] = stats.get("repair_attempts", 0) + 1
                record = {"status": "preview", "source_url": entry.link, "feed_url": entry.feed_url,
                          "content_hash": digest, "source_text": clean_text(entry.content),
                          "article": asdict(article)}
                write_json(Path(config.review_dir) / (key + ".json"), record)
                if dry_run:
                    stats["previews"] += 1
                    observe(entry, "preview")
                    continue
                if article.requires_review or not policy.auto_publish:
                    record["status"] = "held"
                    record["reasons"] = article.review_reasons or ["Source not enabled for automatic publishing"]
                    write_json(Path(config.review_dir) / (key + ".json"), record)
                    if store:
                        store.save(key, digest, {"status":"held", "feed_hash":original_hash,
                            "reason": "; ".join(record["reasons"]), "model_attempts": item_attempts})
                    stats["held"] += 1
                    observe(entry, "held", "; ".join(record["reasons"]))
                    continue
                category_ids, tag_ids = wp.taxonomy(article.category, article.tags, config.category_ids)
                status = publication_status(article, policy, config, category_ids)
                if status != "publish" or not tag_ids:
                    record["status"] = "held"
                    record["reasons"] = article.review_reasons + ([] if category_ids else ["Category mapping needed"]) + ([] if tag_ids else ["No supported tags"])
                    write_json(Path(config.review_dir) / (key + ".json"), record)
                    if store:
                        store.save(key, digest, {"status":"held", "feed_hash":original_hash,
                            "reason": "; ".join(record["reasons"]), "model_attempts": item_attempts})
                    stats["held"] += 1
                    observe(entry, "held", "; ".join(record["reasons"]))
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
                observe(entry, "publish")
            except InsufficientSource as exc:
                stats["skipped"] += 1
                reason = str(exc)
                stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
                write_json(Path(config.review_dir) / (key + ".json"),
                    {"status": "insufficient_source", "source_url": entry.link, "reason": str(exc)})
                # Rejections can be reconsidered when the source, image or prompt changes.
                if store:
                    image_retry = reason.startswith("No eligible featured image") and image_attempts < MAX_IMAGE_ATTEMPTS
                    model_retry = reason == "Missing central facts" and item_attempts < MAX_ITEM_MODEL_ATTEMPTS
                    store.save(key, original_hash, {"status": "image_retry_pending" if image_retry else "retry_pending" if model_retry else "held",
                        "feed_hash":original_hash, "reason":reason, "model_attempts":item_attempts,
                        "image_attempts":image_attempts, "retry_after":time.time() + IMAGE_RETRY_SECONDS})
                observe(entry, "insufficient_source", reason)
            except ModelOutputError as exc:
                stats["held"] += 1
                reason = str(exc)
                stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
                write_json(Path(config.review_dir) / (key + ".json"),
                    {"status":"validation_failed", "source_url":entry.link, "reason":str(exc)})
                if store:
                    store.save(key, original_hash, {"status":"retry_pending" if item_attempts < MAX_ITEM_MODEL_ATTEMPTS else "held",
                        "feed_hash":original_hash, "reason":reason, "model_attempts":item_attempts})
                observe(entry, "validation_failed", reason)
            except Exception as exc:
                stats["errors"] += 1
                # Never include HTTP request objects/headers or keys in logs.
                logger.error("Item held; source=%s error=%s", key[:12], type(exc).__name__)
                write_json(Path(config.review_dir) / (key + ".json"),
                    {"status": "error", "source_url": entry.link, "error_type": type(exc).__name__})
                observe(entry, "error", type(exc).__name__)
        if stats.get("feeds_failed"):
            stats["errors"] += stats["feeds_failed"]
        return stats
    finally:
        if store:
            store.close()
        write_json(Path(config.review_dir) / "run-summary.json", stats)
        write_json(Path(config.review_dir) / "run-items.json", outcomes)
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
