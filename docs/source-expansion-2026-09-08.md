# Source coverage and publishing capacity — September 8, 2026

The FetchRSS account contained 110 feeds, but only 32 were connected to the repository. The other 11 configured feeds all covered Blue Mountain athletics. The latest production report read all 43 successfully, used zero model attempts and deferred zero stories; all 11 sports feeds had zero items within the 24-hour window. The observed sports gap was primarily missing coverage, not an exhausted run limit.

All 78 missing FetchRSS feeds are now connected, including the four sports/podcast pages supplied in the account. No existing feeds were removed. `feeds.txt` names every source in a comment; comments do not affect processing.

## Additional direct feeds

| Source | RSS |
|---|---|
| Mississippi State Athletics | https://hailstate.com/rss |
| Ole Miss Athletics | https://olemisssports.com/rss |
| Southern Miss Athletics | https://southernmiss.com/rss |
| Jackson State Athletics | https://gojsutigers.com/rss |
| Alcorn State Athletics | https://alcornsports.com/rss |
| Mississippi Valley State Athletics | https://mvsusports.com/rss |
| Delta State Athletics | https://gostatesmen.com/rss |
| Mississippi College Athletics | https://www.gochoctaws.com/rss |
| Millsaps Athletics | https://gomajors.com/rss |
| William Carey Athletics | https://careyathletics.com/rss |
| Mississippi High School Activities Association | https://www.misshsaa.com/feed/ |
| Mississippi Department of Environmental Quality | https://www.mdeq.ms.gov/feed/ |

All 133 configured feed URLs returned valid RSS/Atom in the audit. The scan found 85 dated entries in the previous 24 hours, including previously processed stories. This is source availability, not a promise that 85 new articles will publish.

Approved athletics and agency article hosts are listed in `sources.json`. Full article text is read from both current and older athletics templates. An outer ASP.NET form no longer causes the story to be discarded. The source article's own Open Graph featured image is available when the RSS image handler fails. Related-story images are not harvested. Eight active athletics sources tested successfully with fuller story text and an eligible source image. MDEQ's current water-quality notice has no supplied image and therefore remains ineligible under the publisher's featured-image requirement.

## Capacity and complete feed scanning

- Every configured feed is read before publication limits apply, with four concurrent HTTP reads and an individual outcome for every source.
- The default budget is **30 model attempts per run**, raised from 10. A correction attempt counts toward this budget; it is not a guaranteed number of published articles.
- The schedule remains `7,22,37,52 * * * *` (every 15 minutes requested). GitHub may start scheduled jobs late.
- After 600 seconds the processor stops starting fresh work, records remaining stories as deferred and completes its current work. The 30-minute job timeout leaves room to save receipts, the cache and the report. Deferred items can resume next run without becoming permanent holds.
- Processing remains oldest first. The 24-hour freshness window, duplicate receipts, exact quotations, factual verification, featured image, category and tag requirements still apply. Nothing creates WordPress drafts.
- The Actions report now shows configured/read/failed feed totals, the effective model budget, deferred work and already-processed counts per source.

Repository Actions variables can override the defaults. Local `.env` values can also override them when the workflow is run from a local checkout.

## Native feeds checked but not connected

- The State Auditor's native RSS currently has malformed article URLs and undated entries. Its existing FetchRSS feed remains connected.
- The legacy DOJ district RSS links redirect to a national feed and did not preserve the Mississippi district filter in the audit. They must not be added as Mississippi-only feeds without a working filter.
- MSDH's native feed currently exposes a few Certificate of Need PDF reports rather than its complete news-release stream. A feed from the department's actual news/social page is a better coverage candidate.
- NWS Mississippi Atom alerts are valid but have no source image and need explicit expiry/update handling. The account's NWS Jackson and Memphis feeds are now connected for weather reporting; a dedicated live-alert integration should handle the native alert stream.

Validation: 79 offline Python regression tests passed; all 133 configured URLs scanned successfully; eight active direct athletics sources passed the article-text and image preflight. No paid model calls or WordPress writes were made by the preflight.

## Lauderdale and Marion sheriff feeds

Both public Facebook pages were converted in the publisher's FetchRSS account and connected to automatic article generation on September 8. The configured total is now **135 feeds**. The same factual, featured-image, category and tag checks apply before publication.

| Agency | Public Facebook page | Official identity confirmation | FetchRSS | Observed recent activity |
|---|---|---|---|---|
| Lauderdale County Sheriff's Office, Mississippi | https://www.facebook.com/LCSOMS | https://www.lauderdaleso.org/ | https://fetchrss.com/feed/1vcaujD2G3c21x3zElC3f1PW.rss | 10 posts in the past 7 days; latest September 8, 2026 |
| Marion County Sheriff's Office, Mississippi | https://www.facebook.com/marioncountysheriffms/ | https://www.marioncountysheriff.org/ | https://fetchrss.com/feed/1vcaujD2G3c21x3zG9BdX8Kh.rss | 4 posts in the past 7 days; latest September 8, 2026 |

The first production run after the initial 133-feed expansion, [run 11110](https://github.com/wallyrebel/Wordpress/actions/runs/34254026566), read 133/133 successfully, published 11 articles (including six from sports sources), reported zero processing errors, and deferred 64 items when its time budget was reached. Deferred items remain subject to the existing freshness window and publication checks.
