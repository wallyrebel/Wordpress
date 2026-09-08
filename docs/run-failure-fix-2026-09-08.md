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
