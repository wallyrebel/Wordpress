"""Check configured sources from the runner without AI calls or WordPress access."""
import html
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from feed_parser import fetch_feeds_with_raw
from safe_http import canonical_url


def main():
    urls = list(dict.fromkeys(canonical_url(line.strip()) for line in
        (ROOT/'feeds.txt').read_text(encoding='utf-8-sig').splitlines()
        if line.strip() and not line.lstrip().startswith('#')))
    policies = json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
    policies = {canonical_url(url): policy for url, policy in policies.items()}
    stats = {'feeds_configured': len(urls)}
    fetch_feeds_with_raw(urls, stats=stats)
    lines = ['## RSS source connectivity check', '',
        f"Configured: **{len(urls)}** · Read: **{stats.get('feeds_ok', 0)}** · "
        f"Failed: **{stats.get('feeds_failed', 0)}** · Recovered on retry: **{stats.get('feeds_recovered', 0)}**", '',
        'Read-only diagnostic: no AI calls, no WordPress access and no articles published.', '',
        '| Source | Feed | Result | Read attempts |', '|---|---|---|---:|']
    for url, detail in stats['feeds'].items():
        name = policies.get(url, {}).get('publisher') or detail.get('publisher') or url
        result = detail.get('error_type') or ('ok — recovered on retry' if detail.get('recovered_on_retry') else detail['status'])
        cells = [name, url, result, str(detail.get('read_attempts', 1))]
        lines.append('| '+' | '.join(html.escape(value).replace('|','&#124;').replace('\n',' ') for value in cells)+' |')
    report = '\n'.join(lines)+'\n'
    folder=ROOT/'review';folder.mkdir(exist_ok=True)
    (folder/'feed-connectivity.json').write_text(json.dumps(stats,indent=2),encoding='utf-8')
    (folder/'feed-connectivity.md').write_text(report,encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as output:
            output.write(report)
    print(json.dumps({k:v for k,v in stats.items() if k!='feeds'}))
    return 1 if stats.get('feeds_failed') else 0


if __name__=='__main__':
    sys.exit(main())
