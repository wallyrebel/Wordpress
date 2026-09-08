"""Render feed coverage and actionable outcomes in the GitHub job summary."""
import html
import json
import os
from pathlib import Path


def cell(value):
    return html.escape(str(value), quote=True).replace('|', '&#124;').replace('\n', ' ').replace('\r', ' ')


def render_report(stats, items):
    lines = ['## RSS publishing report', '',
        f"Published: **{stats.get('created', 0)}** · Model attempts: **{stats.get('model_attempts', 0)}** · "
        f"Items needing attention or retry: **{stats.get('attention_required', 0)}** · Errors: **{stats.get('errors', 0)}**", '',
        f"Feeds configured: **{stats.get('feeds_configured', len(stats.get('feeds', {})))}** · "
        f"Read successfully: **{stats.get('feeds_ok', 0)}** · Failed: **{stats.get('feeds_failed', 0)}** · "
        f"Deferred: **{stats.get('deferred', 0)}** · Model-attempt limit: **{stats.get('model_attempt_budget', 'unknown')}**", '',
        f"Sources retried after a connection error: **{stats.get('feeds_retried', 0)}** · "
        f"Recovered on retry: **{stats.get('feeds_recovered', 0)}**", '',
        '### Feed coverage', '', '| Source | Read | Eligible | Published | Already processed | Held / retry | Deferred | Old / undated / invalid |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for url, feed in stats.get('feeds', {}).items():
        outcomes = feed.get('outcomes', {})
        holds = sum(count for status, count in outcomes.items()
                    if status not in ('publish', 'preview', 'duplicate', 'deferred'))
        rejected = sum(feed.get(key, 0) for key in ('stale', 'undated_or_future', 'invalid'))
        source = cell(feed.get('publisher') or url)
        lines.append(f"| {source}<br>{cell(url)} | {cell(feed.get('error_type') or feed.get('status', 'unknown'))} | "
            f"{feed.get('eligible', 0)} | {outcomes.get('publish', 0)} | {outcomes.get('duplicate', 0)} | {holds} | {outcomes.get('deferred', 0)} | {rejected} |")
    attention = [item for item in items if item.get('status') not in ('publish', 'preview', 'duplicate')]
    if attention:
        lines += ['', '### Items needing attention or retry', '', '| Source item | Outcome | Reason |', '|---|---|---|']
        for item in attention:
            lines.append(f"| {cell(item.get('title', ''))}<br>{cell(item.get('source_url', ''))} | "
                f"{cell(item.get('status', ''))} | {cell(item.get('reason', ''))} |")
    lines += ['', 'Full item records and evidence are in the news-review artifact. '
              'Deferred items are retried while they remain in the feed and within the configured age window. '
              'Repeated validation failures remain held; no WordPress drafts are created.', '']
    return '\n'.join(lines)


def main():
    folder = Path('review')
    summary = folder / 'run-summary.json'
    if not summary.exists():
        report = '## RSS publishing report\n\nNo processing report was produced. Inspect the failed setup or connection step.\n'
        print('::warning::RSS processing did not produce a run report.')
    else:
        stats = json.loads(summary.read_text(encoding='utf-8'))
        items_path = folder / 'run-items.json'
        items = json.loads(items_path.read_text(encoding='utf-8')) if items_path.exists() else []
        report = render_report(stats, items)
        if stats.get('attention_required') or stats.get('feeds_failed'):
            # Never interpolate untrusted feed text into workflow commands.
            print('::warning::RSS items or feeds need attention. See the publishing report for reasons and retries.')
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'run-report.md').write_text(report, encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as target:
            target.write(report)


if __name__ == '__main__':
    main()
