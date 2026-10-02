# Editorial quality audit and publication gate — October 2, 2026

**Updated policy:** the initial full-article gate below now has verified
public-service brief and multi-source briefing paths. See
[balanced coverage](balanced-coverage.md) for the final publication rules.

The public sample of 100 latest posts covered October 1 at 5:45 p.m. through
October 2 at 8:26 a.m. site time. After excluding `.news-source` attribution,
89 articles had fewer than 100 words, 98 fewer than 200, and 99 fewer than 300.
Median body length was 42.5 words. This is a recent sample, not the whole archive.

Examples include the 30-word Oxford custodians announcement, the 28-word
Lauderdale September reports announcement, and a 15-word Lake football result.
Two Poplarville/McComb posts shared an identical headline and appeared about a
minute apart under separate URLs. The old source-URL receipt alone could not
catch that case.

## Verified implementation causes

- An approved source could enter generation with one word of body text.
- The draft prompt expressly allowed a single short paragraph and had no floor.
- The extractor was told not to judge newsworthiness or information depth.
- No structured five-W completeness check existed; all approved feeds bypassed
  the Mississippi-relevance rejection.
- A recent `updated` date could revive a stale original publication.
- The schedule ran every 15 minutes and allowed 30 model attempts per run.

## Changes

1. Reject sources below 180 words before downloading images or spending on AI.
2. Require seven distinct, exact-evidence-backed facts and explicit fact mappings
   for who, what, where, when and why. Require substantive local reader value.
3. Require at least 300 body words and four paragraphs; headline, excerpt and
   source link do not count. Reject repeated sentences and missing body coverage.
4. Require the independent draft-verification response to pass both factual and
   editorial checks. Generic expansion, invented motives and padded prose fail.
5. Revalidate the final article/evidence match before WordPress mutations.
6. Search WordPress for exact normalized duplicate headlines; fail closed on
   lookup errors. This also works after loss of the runner cache. It is not
   semantic duplicate detection and does not consolidate the old archive.
7. Use original publication time for freshness when present.
8. Run every two hours, default to eight model attempts and three per feed, and
   default manual dispatch to preview. Preserve concurrency and durable receipts.

The word thresholds are the publisher's editorial policy, not an SEO promise.
[Google's helpful-content guidance](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)
does not prescribe a preferred word count. Original reporting and useful details
matter; longer paraphrases alone do not establish value or guarantee ranking.

## Operational checks

Run `python -m unittest discover -s tests -v` and the existing PHP contract checks.
Inspect a manual dry run before increasing throughput. A successful run may
publish nothing when no source qualifies. Review the rejection reasons rather
than relaxing gates to meet a posting quota. Scheduled runs use the default
branch; local changes alone do not deploy.

The companion plugin remains compatible and retains source/version receipts.
These new gates execute in the publishing client; an unrelated writer using
WordPress directly is outside their scope. Existing posts are preserved for
editorial review. Private account measurements are retained outside this public
repository in the user's local audit report.

## Follow-up editorial work

Prioritize original Mississippi reporting, service journalism with dates and
practical details, and substantial primary releases. Review thin indexed pages
individually using traffic, backlinks, source evidence and currency before
consolidating or removing them. Preserve valuable URLs and use relevant redirects
only where an equivalent replacement exists. Review unresolved missing-person
and crime updates promptly with the issuing authority; a comment alone is not
evidence that an allegation or case status has changed.
