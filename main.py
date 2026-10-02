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
from ai_rewriter import (rewrite_article, fingerprint, clean_text, check_source_length, editorial_format, word_count,
                         validate_publication_article, InsufficientSource, ModelOutputError, PROMPT_VERSION)
from config import load_config
from database import Store
from feed_parser import fetch_feeds_with_raw, enrich_entry
from image_handler import get_source_image, IMAGE_POLICY_VERSION
from wordpress_api import WordPressAPI, safe_http_details
from roundups import rewrite_roundup, candidate_group

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
    started = time.monotonic()
    stats = {"created": 0, "previews": 0, "held": 0, "skipped": 0, "errors": 0,
             "duplicates": 0, "source_updates": 0, "model_attempts": 0,
             "reasons": {}, "model_attempt_budget": limit or config.max_posts_per_run,
             "max_run_seconds": config.max_run_seconds, "feeds_configured": len(config.rss_feeds)}
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
        entries.sort(key=lambda item: (editorial_format(item[0].content) != 'brief',
                     item[0].published or item[0].updated or datetime.max.replace(tzinfo=timezone.utc)))
        roundup_possible = sum(35 <= word_count(e.content) < 180 and editorial_format(e.content) == 'roundup'
                               for e,_ in entries) >= 3
        seen = set()
        attempted = 0
        feed_attempts = {}
        roundup_candidates = []
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
            stage = "publication receipt and cache"
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
                    stage = "legacy source enrichment"
                    entry = enrich_entry(entry, policy, raw)
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
                if time.monotonic() - started >= config.max_run_seconds:
                    stats["time_budget_reached"] = True
                    stats["deferred"] = stats.get("deferred", 0) + 1
                    observe(entry, "deferred", "Run time budget reached; retry next run")
                    continue
                stage = "source article enrichment"
                entry = enrich_entry(entry, policy, raw)
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
                # Thin sources must not consume images/model budget and hide better items.
                stage = "source quality gate"
                format = editorial_format(entry.content)
                if format == "roundup":
                    if word_count(entry.content) >= 35 and policy.auto_publish:
                        roundup_candidates.append((entry, raw, policy, original_hash, digest))
                        continue
                    check_source_length(entry.content)
                if format == "full":
                    check_source_length(entry.content)
                total_budget = limit or config.max_posts_per_run
                # Reserve one bounded pipeline for combining useful smaller items.
                standalone_budget = max(1, total_budget - int(roundup_possible))
                if attempted >= standalone_budget:
                    stats["budget_reached"] = True
                    stats["deferred"] = stats.get("deferred", 0) + 1
                    observe(entry, "deferred", "Run model-attempt budget reached; retry next run")
                    continue
                if feed_attempts.get(entry.feed_url, 0) >= config.max_entries_per_feed:
                    stats["deferred"] = stats.get("deferred", 0) + 1
                    observe(entry, "deferred", "Per-feed model-attempt budget reached; retry next run")
                    continue
                image_attempts += 1
                stage = "source image selection"
                image = get_source_image(raw, policy, config.image_dir)
                if not image and format != 'brief':
                    raise InsufficientSource("No eligible featured image (minimum 600px wide and 400px high)")
                # Limit paid model attempts, not deduplication, source updates or
                # missing-image checks. All publication checks still apply.
                while True:
                    stage = "article generation and verification"
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
                            correction_feedback=correction_feedback, format=format)
                        break
                    except (ModelOutputError, InsufficientSource) as exc:
                        repairable = isinstance(exc, ModelOutputError) or str(exc) == "Missing central facts"
                        if (not repairable or item_attempts >= MAX_ITEM_MODEL_ATTEMPTS
                                or attempted >= standalone_budget
                                or feed_attempts[entry.feed_url] >= config.max_entries_per_feed
                                or time.monotonic() - started >= config.max_run_seconds):
                            raise
                        correction_feedback = str(exc)
                        stats["repair_attempts"] = stats.get("repair_attempts", 0) + 1
                if policy.category:
                    article = replace(article, category=policy.category)
                validate_publication_article(article)
                record = {"status": "preview", "source_url": entry.link, "feed_url": entry.feed_url,
                          "content_hash": digest, "source_text": clean_text(entry.content),
                          "article": asdict(article)}
                write_json(Path(config.review_dir) / (key + ".json"), record)
                if dry_run:
                    stats["previews"] += 1
                    stats[format + '_previews'] = stats.get(format + '_previews',0)+1
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
                stage = "duplicate headline check"
                duplicate = wp.find_duplicate_headline(article.headline)
                if duplicate:
                    record.update(status="duplicate", duplicate_post_id=duplicate)
                    write_json(Path(config.review_dir) / (key + ".json"), record)
                    store.save(key, digest, {"status": "duplicate", "post_id": duplicate,
                        "feed_hash": original_hash, "reason": "Exact headline already published"})
                    stats["duplicates"] += 1
                    observe(entry, "duplicate", "Exact headline already published")
                    continue
                stage = "WordPress category and tags"
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
                stage = "WordPress featured image upload"
                media_id = wp.upload_media(image, article.headline) if image else 0
                if image and not media_id:
                    raise ValueError("Featured image upload failed")
                publisher = policy.publisher or "original source"
                source_line = '<p class="news-source">Source: <a href="' + html.escape(
                    entry.link, quote=True) + '">' + html.escape(publisher) + "</a>.</p>"
                stage = "WordPress article publication"
                receipt = wp.upsert({
                    "source_key": key, "content_hash": digest, "source_url": entry.link,
                    "title": article.headline, "content": article.body + source_line,
                    "excerpt": article.excerpt, "status": status,
                    "categories": category_ids, "tags": tag_ids, "featured_media": media_id,
                    "review_reasons": article.review_reasons + ([] if category_ids else ["Category mapping needed"]),
                    "source_published": (entry.published or entry.updated).isoformat(),
                    "evidence": article.evidence})
                stage = "publication receipt persistence"
                store.save(key, digest, {**receipt, "feed_hash": original_hash})
                record["status"], record["receipt"] = receipt["status"], receipt
                write_json(Path(config.review_dir) / (key + ".json"), record)
                stats["created"] += 1
                stats[format + '_created'] = stats.get(format + '_created',0)+1
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
                details = safe_http_details(exc)
                reason = stage + ": " + type(exc).__name__
                if details:
                    reason += " HTTP " + str(details["http_status"])
                    if details.get("api_error_code"):
                        reason += " (" + details["api_error_code"] + ")"
                logger.error("Item held; source=%s error=%s", key[:12], reason)
                write_json(Path(config.review_dir) / (key + ".json"),
                    {"status": "error", "source_url": entry.link, "stage": stage, "error_type": type(exc).__name__, **details})
                observe(entry, "error", reason)
        if roundup_candidates:
            run_roundup(config, roundup_candidates, stats, started, dry_run, client, wp, store, observe)
        if stats.get("feeds_failed"):
            stats["errors"] += stats["feeds_failed"]
        return stats
    finally:
        stats["elapsed_seconds"] = round(time.monotonic() - started, 2)
        if store:
            store.close()
        write_json(Path(config.review_dir) / "run-summary.json", stats)
        write_json(Path(config.review_dir) / "run-items.json", outcomes)
        logger.info("Run summary: %s", json.dumps(stats))


def run_roundup(config, candidates, stats, started, dry_run, client, wp, store, observe):
    """Combine smaller items, with durable per-source deduplication and a shared budget."""
    selected = []
    sources = []
    key = None
    reported = set()
    def report(entry,status,reason):
        reported.add(source_key(entry))
        observe(entry,status,reason)
    try:
        if stats['model_attempts'] >= stats['model_attempt_budget'] or time.monotonic()-started >= config.max_run_seconds:
            raise InsufficientSource('Roundup deferred until next run: processing budget reached')
        # Prefer enough factual material; cap each feed's contribution for source diversity.
        groups = {}
        for item in candidates:
            group = candidate_group(item[0],item[2])
            if group:
                groups.setdefault(group,[]).append(item)
        pools = [items for items in groups.values() if len(items) >= 3]
        if not pools:
            raise InsufficientSource('Roundup waiting for three compatible non-sensitive items')
        pool = max(pools,key=lambda items:sum(sorted((word_count(i[0].content) for i in items),reverse=True)[:6]))
        counts = {}
        for item in sorted(pool, key=lambda c: (-word_count(c[0].content), c[0].link)):
            if time.monotonic()-started >= config.max_run_seconds:
                raise InsufficientSource('Roundup deferred until next run: processing budget reached')
            entry, raw, policy, original_hash, digest = item
            if counts.get(entry.feed_url,0) >= 2:
                continue
            if wp:
                # Recover coverage even if a runner died between publishing a roundup
                # and recording all member receipts, or the local DB was lost.
                covered = wp.find_source_publication(entry.link)
                if covered:
                    receipt = wp.upsert({'source_key':source_key(entry),'content_hash':digest,
                        'source_url':entry.link,'adopt_post_id':covered})
                    store.save(source_key(entry),digest,{**receipt,'feed_hash':original_hash})
                    stats['duplicates'] += 1
                    report(entry,'duplicate','Source already included in a published story or briefing')
                    continue
            selected.append(item)
            counts[entry.feed_url] = counts.get(entry.feed_url,0)+1
            sources.append({'source_id':source_key(entry),'url':entry.link,'title':entry.title,
                'publisher':policy.publisher,'source_date':(entry.published or entry.updated).isoformat(),
                'approved_primary_source':policy.reuse_allowed and policy.auto_publish,
                'text':clean_text(entry.content)})
            if len(selected) == 6:
                break
        if len(selected) < 3 or sum(word_count(s['text']) for s in sources) < 250:
            raise InsufficientSource('Roundup waiting for at least three useful items and 250 source words')
        key = hashlib.sha256(('roundup:'+','.join(sorted(s['source_id'] for s in sources))).encode()).hexdigest()
        stats['model_attempts'] += 1
        stats['roundup_attempts'] = stats.get('roundup_attempts',0)+1
        article = rewrite_roundup(sources,client,config.extraction_model,config.drafting_model)
        validate_publication_article(article)
        used = {s['source_id'] for s in article.evidence['sources']}
        included = [item for item in selected if source_key(item[0]) in used]
        record = {'status':'preview','article':asdict(article),'source_urls':[s['url'] for s in article.evidence['sources']]}
        write_json(Path(config.review_dir)/(key+'.json'),record)
        if dry_run:
            stats['previews'] += 1
            stats['roundup_previews'] = stats.get('roundup_previews',0)+1
            for entry,*_ in included:
                report(entry,'preview','Included in verified multi-source briefing')
            return
        if wp.find_duplicate_headline(article.headline):
            raise InsufficientSource('A briefing for this section and compilation date is already published')
        image = next((found for _,raw,policy,_,_ in included
                      if (found := get_source_image(raw,policy,config.image_dir))),None)
        category_ids, tag_ids = wp.taxonomy(article.category,article.tags,config.category_ids)
        if not category_ids or not tag_ids:
            raise InsufficientSource('Roundup category or supported tags missing')
        media_id = wp.upload_media(image,article.headline) if image else 0
        if image and not media_id:
            raise ValueError('Roundup featured image upload failed')
        digest = fingerprint(article.headline,json.dumps(article.evidence['sources'],sort_keys=True))
        receipt = wp.upsert({'source_key':key,'content_hash':digest,'source_url':included[0][0].link,
            'title':article.headline,'content':article.body,'excerpt':article.excerpt,'status':'publish',
            'categories':category_ids,'tags':tag_ids,'featured_media':media_id,'review_reasons':[],
            'source_published':(included[0][0].published or included[0][0].updated).isoformat(),
            'evidence':article.evidence})
        stats['created'] += 1
        stats['roundups_created'] = stats.get('roundups_created',0)+1
        record.update(status='publish',receipt=receipt)
        write_json(Path(config.review_dir)/(key+'.json'),record)
        for entry,_,_,original_hash,member_digest in included:
            adopted = wp.upsert({'source_key':source_key(entry),'content_hash':member_digest,
                'source_url':entry.link,'adopt_post_id':receipt['post_id']})
            store.save(source_key(entry),member_digest,{**adopted,'feed_hash':original_hash})
            report(entry,'publish','Included in multi-source briefing')
    except (InsufficientSource,ModelOutputError) as exc:
        stats['roundup_holds'] = stats.get('roundup_holds',0)+1
        stats['reasons'][str(exc)] = stats['reasons'].get(str(exc),0)+1
        for entry,*_ in selected or candidates:
            report(entry,'roundup_pending',str(exc))
        if key:
            write_json(Path(config.review_dir)/(key+'.json'),{'status':'roundup_pending','reason':str(exc),
                'sources':sources,'diagnostics':getattr(exc,'evidence',{})})
    except Exception as exc:
        stats['errors'] += 1
        logger.error('Roundup held: %s',type(exc).__name__)
        for entry,*_ in selected or candidates:
            report(entry,'error','Roundup pipeline: '+type(exc).__name__)
    finally:
        for entry,*_ in candidates:
            if source_key(entry) not in reported:
                stats['deferred'] = stats.get('deferred',0)+1
                observe(entry,'roundup_pending','Not selected for this briefing; reconsider while source remains current')

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
