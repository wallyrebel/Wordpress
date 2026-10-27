# Controlled RSS restoration — October 27, 2026

The publisher approved repairing and re-enabling **only** the existing RSS workflow
220722126, with a goal of five distinct useful verified articles per America/Chicago
calendar day. Existing posts, URLs, dates, sources, rights and editorial holds are preserved.
The unidentified briefing task is not disabled or replaced by a second scheduled writer.

## Daily accounting and reconciliation

The publisher discovers the post statuses accepted by the site's REST schema and reads
each explicitly using authenticated `context=edit`. A missing status schema, denied read,
timeout, malformed response or pagination bound stops publication. Recent inventory covers
posts modified in the past 35 days and fixed existing posts **33889/33891**. Candidate source,
headline and event searches span all dates and all supported statuses. Draft/private text is
never sent to the semantic event checker or included in public diagnostic receipts.

All source anchors count, including links embedded by the other producer in a briefing;
coverage is no longer limited to `.news-source` links or published posts. Exact source URLs,
normalized headlines and possible matching events are reconciled before media/taxonomy
writes. A possible unpublished/private match or uncertain event match is held for review.
The inventory is refreshed immediately before writing. Existing server source locks and
receipts, plus the repository's `ms-news-publishing` concurrency group, remain authoritative.
An independent external writer cannot share the Actions concurrency group, so the fresh
pre-write reconciliation is essential; it is not a claim of an atomic lock across that task.

Distinct `publish` post IDs are counted from authoritative UTC publication times converted
to America/Chicago, including DST. A multi-section briefing counts once. Counts before/after,
anchor states, supported statuses and remaining shortfall are saved in reconciliation artifacts
and the job summary. A shortfall is a warning requiring source/hold/throughput review, never
permission for filler, fabricated context, duplicated coverage or weakened evidence gates.

The two-hour UTC cadence `17 */2 * * *` and eight-pipeline attempt budget are unchanged.
New publication is limited to two posts per run to avoid bulk catch-up. Five is a daily goal,
not a claim that eight model attempts produce eight articles or that every day has enough
qualifying source material. Additional useful distinct coverage can continue after the goal.

## Content and model changes

Extraction, drafting and verification now use **gpt-6-luna**, with existing bounded output
caps, low extraction reasoning, no drafting reasoning and stronger sensitive verification.
Official Standard pricing: $0.10 input, $0.01 cached input and $0.50 output per million tokens.
See https://developers.openai.com/api/docs/models/gpt-6-luna . No local API keys are required;
existing managed credentials stay in GitHub Actions. Event comparisons are additional bounded
low-reasoning checks, at most ten candidate records per comparison, with 1,800 output tokens.

Allegations, criminal-case coverage and private sensitive information now require separate
editorial approval even when an official source supplied them. They cannot pass into routine
publication merely because verification succeeds. Missing-information placeholders such as
"not provided", "not specified", unknown dates/locations and TBD/TBA are rejected rather than
published. Useful source-quality holds and all original evidence, freshness and image gates
remain in place. No new image-reuse permission is inferred.

## Preview, activation and verification

The first deployment keeps `PUBLISHER_LIVE: 'false'` in the existing RSS workflow. This makes
any scheduled/manual run an **authenticated preview**: WordPress GET/OPTIONS reconciliation,
model/editorial previews and local images only. It creates no posts, media, tags, source receipts
or publishing database updates. Review `reconciliation-before/after.json` and exact source,
article and image artifacts before switching live mode on.

`rss-repair-preview.yml` is a temporary, read-only validation workflow, not a scheduled writer.
It uses `contents: read`, shares the publishing concurrency group, and runs on an explicit
change to its own file or manual dispatch. It runs offline tests before a three-pipeline
authenticated preview. It grants no additional GitHub token permissions. Remove it after
restoration has been validated; the normal publisher's manual default remains preview.

After successful offline checks, authenticated reconciliation, source/editorial/image review,
enable only workflow 220722126 and change its deployment gate to `PUBLISHER_LIVE: 'true'`.
Observe the next exact scheduled Action. No other workflow or schedule needs enabling.

Each post receipt is persisted before independent public verification. The saved raw payload,
public body, title/H1, description, self-canonical, robots metadata/headers, source links and
featured-image pixels must match before reporting verified success. The worker does not claim
to write SEO plugin fields. On any ambiguous result retain the source identity and receipt,
reconcile that post and never blindly recreate it. A failed public verification is reported
with the known post receipt, rather than represented as a successful publication.
