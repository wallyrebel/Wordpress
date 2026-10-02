# Balanced coverage policy

The October 2 rollout was revised after the publisher requested a balance between
depth and coverage. A single 300-word minimum is no longer applied to every story.

- **Full articles:** 180+ source words, seven distinct evidence-backed facts,
  all five Ws, 300+ body words and four useful paragraphs. Never add padding.
- **Public-service briefs:** concrete alerts, service changes, assistance notices
  and crime developments may use 70–250 words and two or more paragraphs. They
  need 45+ source words, four distinct facts, who/what/where/when, and independently
  verified public-service value. Report purpose or impact when supplied; never
  invent a motive. Missing why alone does not block a useful brief. Keyword
  routing does not waive the extractor or verifier's substantive-value checks.
- **Multi-source briefings:** combine 3–6 useful smaller items from at least two
  publishers into 250–900 supported body words. Each section needs its own facts,
  30–200 body words,
  dates, location, headline and source link. Evidence, numbers and quotations are
  checked against that section's source, then the whole draft is verified for
  accuracy, relevance, duplicate events and useful detail. Sensitive crime,
  emergency and medical items are excluded from general roundups.
  Invalid extracted sections are omitted individually; at least three valid
  sections must remain before drafting. Rejected sources are not sent to the
  writer. A malformed item cannot supply facts or block unrelated valid sections.

Brief candidates receive priority. One of the existing bounded model pipelines
is reserved for a roundup when the feeds contain enough smaller candidates.
Short greetings, teasers, photo captions and unsupported claims still do not
qualify. A run can publish zero when the current source material does not meet
any format; there is no quota that overrides evidence or freshness.

Verified service briefs and multi-source briefings may publish without a photo.
Any attached photograph still requires reuse permission and the existing image
quality checks. No unrelated stock image or artificial incident photo is used.
Install companion plugin 1.0.2 before enabling this exception in production.

Source receipts prevent republishing members of a briefing as new standalone
stories. Exact source-attribution lookup recovers coverage if a runner stopped
before writing every member receipt. The briefing date uses America/Chicago,
including daylight saving time, and is supplied to the writer and verifier for
freshness checks. It is a compilation date,
not the date of every event. At most one community and one sports briefing with
the same compilation date can pass the exact-headline publication check.

The two-hour schedule and default eight-pipeline budget remain. Run artifacts
show full articles, briefs, roundups, previews and holds separately. Review a
live dry run before deployment and inspect resulting articles after publication.
