# RSS to WordPress: checked automatic publication

This workflow uses **GPT-5 Nano** to extract source-backed facts, **GPT-5.6 Luna** to write the article, and **GPT-5 Nano** to check the draft against the original source.

The publisher has approved all configured RSS feeds as factual, first-person sources for rewriting and quoting. Enabled feeds do not require separate source approval, independent corroboration, or an explicit Mississippi mention in every entry. Verification checks the rewrite against the source account; factual fidelity, exact quotations, attribution, images and taxonomy remain required.

Article length: aim for 300–500 body words when the source supports a full article. Extract and cover all useful material details from substantial reports. Short notices can remain shorter; do not add repetition, invented context or stretched quotations to meet a word count. This is an editorial target, not a Google minimum or a publication gate. The existing maximum length and factual checks still apply.

Approved primary-source briefs with at least eight source words can proceed to fact extraction even when shorter than 20 words. Concrete facts, supported entity tags, a usable source image and full factual verification are still required; titles alone do not qualify. Numeric validation accepts equivalent time formatting such as `1:56am` and `1:56 a.m.` while rejecting changed minutes or hours. The prompt version changes when these checks change, so cached rejections are reconsidered without republishing articles that already have WordPress receipts.

It publishes automatically only when every requirement passes:

- A current, dated source from your configured feed list.
- Mississippi relevance and concrete, source-supported facts.
- Evidence quotations that actually occur in the source. Extra wrapping quotation marks are removed only when the enclosed excerpt matches the source.
- Story text is summarized; any direct quotations retain exact source wording and attribution. Altered quotations are blocked. The writer and verifier receive the same source URL and publisher.
- A headline, excerpt and body that pass structure, numeric and factual checks.
- No unresolved factual issues. Sensitive subjects receive a stronger verification pass and can publish when they pass. Source corrections are held locally.
- A decoded, usable featured image at least 600 pixels wide and 400 pixels high; 1,200-pixel sources are preferred when supplied. Images are never enlarged to pass validation.
- An existing WordPress category and at least one supported entity tag.
- Successful image upload and server-side validation.

**No WordPress drafts are created.** Failed items go to local JSON review records, available as GitHub Actions artifacts. The generated text is an internal draft only until checks pass.

## Setup

1. Install Python 3.11+ and run `python -m pip install -r requirements.lock`.
2. Retain your existing `.env`, `feeds.txt` and `processed.db`. New installations can copy `.env.example`.
3. Install and activate the bundled plugin folder `wordpress-plugin/ms-news-workflow` in WordPress. It supplies durable receipts and a lock for each source, independent of GitHub cache availability. The application-password user needs edit-others-posts, publish-posts, upload-files and taxonomy permissions.
4. The supplied `category-map.json` maps to the existing MS News Group categories, verified through the live REST API. `CATEGORY_IDS_JSON` can override individual IDs in `.env` or GitHub repository variables. With no mapping, the client tries exact names; Crime & Courts maps to Crime, Politics to Mississippi Politics, and Sports uses the current Sports archive. It never creates categories.
5. Review `sources.json` overrides. Existing feeds remain enabled as your configured input sources. Source text and source images are used under your existing reuse arrangements; the software cannot establish ownership or licensing. Disable sources/images you cannot reuse. Publisher identity defaults to the feed title, falling back to source hostname; override it when the feed title is vague.
6. Run `python scripts/preflight.py` for a read-only connection/category/companion check.
7. Run `python -m unittest discover -s tests -v`.
8. Run `python main.py --dry-run --max-items 3` to generate local previews using the API. This does not contact WordPress or alter the publishing database.
9. Run `python main.py --max-items 3` to publish qualifying items.

No live deployment occurs merely by editing the local files. GitHub Actions uses the version pushed to its default branch; the companion must be active first.

## Configuration

See `.env.example`. Defaults:

| Setting | Default |
|---|---|
| EXTRACTION_MODEL | gpt-5-nano |
| DRAFTING_MODEL | gpt-5.6-luna |
| PUBLISH_MODE | auto |
| MAX_POSTS_PER_RUN | 10 |
| MAX_ENTRIES_PER_FEED | 25 |
| MAX_AGE_HOURS | 24 |
| POLL_INTERVAL_MINUTES | 15 |

Nano uses low reasoning for extraction and ordinary verification, and medium for sensitive-subject verification; Luna uses none. Responses have explicit output-token caps, strict Pydantic Structured Outputs, a 90-second client timeout and at most two SDK retries. There is no silent fallback to another drafting model. Each accepted article records actual token usage in its evidence packet. Reasoning tokens count toward output usage.

`feeds.txt` stays primary, supports comments and UTF-8 BOM, and preserves the original feed order. `RSS_FEEDS` is used only if the file is absent. `FEEDS_FILE` and `SOURCES_FILE` can override paths. The misleading RSS_FEEDS secret was removed from the Actions workflow.

## Source overrides

Copy entries from `sources.example.json` into `sources.json`, keyed by the exact feed URL. A source may be disabled with both `reuse_allowed: false` and `auto_publish: false`.

Full-page scraping is off by default. Enable `allow_scrape` only for sources you use under a suitable arrangement and explicitly list `article_hosts`. Fetching has byte limits, timeouts, redirect validation and private-address checks. The private-address checks reduce SSRF exposure but are not a substitute for network-level egress controls against DNS rebinding.

Images come from the feed's media and image candidates; up to five candidates are checked. Small, corrupt, oversized or non-image responses are rejected. Pexels guessing was removed: an unrelated stock picture should not pass as an event photograph. Use `image_credit` for an actual supplied photographer/provider credit; otherwise the caption labels the source, without asserting ownership.

Tags are limited to up to five names of people, organizations and places that occur in the source. For an approved first-person source with no usable names in its text, the known feed/configured publisher supplies the source tag. This supports complete safety advisories that do not repeat the department's name; it never supplies missing incident facts or unidentified subjects. Existing tags are reused; new source-grounded entity tags can be created. Dates and numeric strings are excluded. Category names come from a fixed allowlist and are mapped to existing site sections.

## Reliability and migration

- The WordPress companion stores persistent receipts by normalized source URL and content hash.
- An atomic source lock prevents overlapping publishing requests from creating duplicates.
- Receipt retries return the existing post even after the local cache is lost.
- A source update is held locally with the original post ID. It never silently overwrites an editor's changes or creates a draft.
- The old SQLite `processed_entries` table is preserved. Existing GUID/post pairs are adopted into server receipts without republishing.
- Validation failures get one correction attempt using the failure reason and original evidence; every attempt repeats all factual, numeric and quotation checks. Both attempts count toward the existing run/per-feed model budgets. If that budget is exhausted, the second attempt resumes next run. After two failed attempts for an unchanged input, the item stays held and its reason remains visible without repeated model charges.
- A missing/temporarily unavailable source image is checked up to three times, at least 30 minutes apart, before staying held. Image checks do not spend model tokens. Changed source/image input, prompts, models or policy can reopen a held item.
- Eligible entries across all feeds are processed oldest first, rather than always favoring the top of `feeds.txt`. Items beyond the model budget are explicitly reported as deferred for the next run. The freshness window still applies: deferred items must remain available in the feed and within `MAX_AGE_HOURS` (24 by default). Persistent deferrals call for reviewing throughput or the window.
- Approved-source text has no arbitrary minimum word count. Even a short complete notice goes through extraction and verification; empty text, placeholders and unsupported expansions cannot publish.
- Specific safety advisories can publish as attributed briefs without inventing an incident, date or campaign. If the writer adds a sentence period inside an otherwise exact quotation, the period moves outside the quotation before verification; changed quoted words remain blocked.
- Existing-post updates are cached and do not consume the model-attempt budget. Legacy adoption records the source content hash, so rotating feed metadata does not become a false article correction. Missing-image checks also do not consume paid model attempts; run summaries include rejection reasons.
- Image upload failures, API failures and failed checks never fall through to publication.
- The Actions schedule runs every 15 minutes, offset from the hour. A concurrency group prevents overlap; the job has a 20-minute limit. Partial feed failures produce a failing run instead of a misleading green success.
- Dependencies are locked; CI runs Python regression tests plus PHP syntax and companion contract tests.
- Logs, evidence records and a run summary are retained as artifacts for 14 days. Every run lists each configured feed, its read status, eligible/old/invalid counts and publication outcomes. Held and deferred items also produce a GitHub warning and a readable job summary, including holds restored from the database cache. No credentials are written to them.
- Email sending was removed from the processing path. The old email helper remains as an unused historical module. Use GitHub Actions notification settings for run failures.

A workflow cannot alert when GitHub stops scheduling it entirely. An independent uptime/heartbeat monitor is still needed for that failure mode. This implementation does not claim to solve Google scheduler outages.

## Review and recovery

`review/<source-key>.json` records the reason for held or rejected items, or the article/evidence after publication. `review/run-summary.json` provides counts and per-feed coverage; `review/run-items.json` lists every eligible source item's outcome. `python scripts/run_report.py` creates `review/run-report.md` and appends it to the GitHub job summary when run in Actions. Cached holds remain visible even when only the database was restored. Review records contain source text and should be treated as editorial working files; they are gitignored.

A companion lock deliberately does not expire automatically: a timed-out client could still have a live server operation. If `source_locked` persists, an administrator should inspect the original post, the `_msn_source_key` and `_msn_content_hash` metadata, and the `msn_receipt_<key>` option before clearing `msn_lock_<key>`. If a post exists but its receipt was not saved, repair the receipt first. Blindly deleting locks can reintroduce duplicate publication.

After fixing an image/category/source policy, remove only the relevant entry from the local `news_receipts` cache to reconsider a held item. Do not erase the legacy table. WordPress receipts remain authoritative for already published items.

## Evaluation

`python scripts/evaluate.py` runs five **synthetic, fictional** fixtures through the live API, saves output and traces locally, and never contacts WordPress. These fixtures are test data, not publishable news. Add real, licensed examples and review factuality, attribution, allegation qualifiers, headlines and cost before increasing throughput.

Automated checks reduce errors; they cannot prove every news claim true. The verification model checks support in the supplied source, not the real-world truth of that source. Original reporting, source quality, editorial accountability and a usable website still matter for search performance.

Official API references:
- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [GPT-5 Nano](https://developers.openai.com/api/docs/models/gpt-5-nano)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
