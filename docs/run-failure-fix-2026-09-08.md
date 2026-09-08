# September 8 publishing-run failures

Runs [11111](https://github.com/wallyrebel/Wordpress/actions/runs/34256519185) and
[11112](https://github.com/wallyrebel/Wordpress/actions/runs/34257455734) each read
all 135 feeds with zero feed failures. They published 15 and 16 articles,
respectively. Both reported one identical processing error.

The HailState RSS item "WATCH: Football Win Over ULM Highlights" redirects from
its approved athletics article URL to a YouTube video. Its RSS body contains
an image without article text. The enrichment code rejected the off-host result
with an ordinary ValueError. That made the completed run return exit code 1,
and the uncached error repeated in subsequent runs.

Unsupported off-host redirects and non-HTML documents now produce an explained
source hold. The existing hold cache prevents repeat attempts until the source
or relevant configuration changes. No model calls or WordPress drafts are
created for that unsupported item. Other eligible articles continue publishing.
The article host allowlist, factual verification, image and taxonomy requirements
remain enforced. Real network, model API and WordPress failures still fail runs.

Unexpected errors now identify the processing stage without logging exception
messages, credentials or request headers. Literal URL text no longer creates a
misleading Beautiful Soup HTML-parser warning.

Validation: 87 offline tests passed, including a video redirect followed by a
successfully published story, a second run using the cached hold, unsupported
document handling, and a genuine error that still fails with a safe stage label.
Related pending source work in this change adds tested CivicEngage article-body
extraction, WordPress featured-photo fallback and optional dedicated-source
category routing. Source additions are documented separately.

## Live verification and intermittent source connectivity

Run [11113](https://github.com/wallyrebel/Wordpress/actions/runs/34258563621)
had already started on the old code. It published five articles and failed on
the same video redirect; it did not reveal another item-processing error.

The first run with the fix,
[11114](https://github.com/wallyrebel/Wordpress/actions/runs/34260013202), correctly
reported that video as an insufficient-source hold instead of an error. It
published two articles with zero item-processing errors, but the MHSAA native
RSS feed had a ConnectTimeout from the GitHub runner: 154/155 sources were read.
This is a separate transport failure, not a recurrence of the video defect.

Transport failures now receive a second read pass after the other sources,
using fresh connections. Successful sources are not downloaded again. Reports
identify sources retried and recovered; unresolved feed failures still produce
a failed run. Regression tests exercise both recovery and persistent failure.
The optional **RSS source connectivity check** Actions workflow checks all
configured feeds from GitHub without any OpenAI or WordPress credentials/calls,
so connection problems can be diagnosed without spending on rewriting.

Validation after the retry improvement: 89 offline tests passed.

The [GitHub source-only diagnostic](https://github.com/wallyrebel/Wordpress/actions/runs/34261782704)
confirmed MHSAA's native site still timed out after both passes, while the other
154 sources worked. The workflow now uses MHSAA's verified official Facebook
channel through FetchRSS instead of connecting to that unreachable website RSS.
The alternate supplied six updates in the past month, readable source text and
an eligible source photo. Sports routing is retained. The configured total
remains 155. This retains the organization's official social coverage; it does
not imply its Facebook page mirrors every website entry.
