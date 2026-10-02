import json
import unittest
from pathlib import Path
from unittest.mock import patch

from main import apply_feed_health, run_exit_code, run_feed_processing
from scripts.run_report import render_report
import test_workflow


class RunHealthTests(unittest.TestCase):
    def test_single_source_timeout_does_not_fail_other_154_sources(self):
        fixture = test_workflow.PublishingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.cfg.rss_feeds = [f'https://example.org/feed-{i}' for i in range(155)]
        xml = b'<rss version="2.0"><channel><title>Source</title></channel></rss>'
        def read(url):
            if url == fixture.cfg.rss_feeds[0]:
                raise TimeoutError()
            return xml, 'application/rss+xml', url
        with patch('feed_parser.fetch_bytes', side_effect=read):
            stats = run_feed_processing(fixture.cfg, client=object(), wp=fixture.wp)
        self.assertEqual(stats['feeds_ok'], 154)
        self.assertEqual(stats['feeds_failed'], 1)
        self.assertEqual(stats['errors'], 0)
        self.assertEqual(run_exit_code(stats), 0)
        saved = json.loads((Path(fixture.cfg.review_dir) / 'run-summary.json').read_text())
        self.assertEqual(saved['feed_health'], 'degraded')
        report = render_report(saved, [])
        self.assertIn('Partial source outage', report)
        self.assertIn('TimeoutError', report)
        self.assertIn(fixture.cfg.rss_feeds[0], report)

    def test_coverage_boundary_and_total_outage(self):
        for successful, failed, expected in [(9, 1, 0), (8, 2, 1), (0, 1, 1), (0, 155, 1)]:
            with self.subTest(successful=successful, failed=failed):
                stats = {'feeds_configured': successful + failed,
                         'feeds_ok': successful, 'feeds_failed': failed, 'errors': 0}
                apply_feed_health(stats)
                self.assertEqual(run_exit_code(stats), expected)
                if expected:
                    self.assertIn('Feed coverage failure', render_report(stats, []))

    def test_publishing_error_still_fails_with_healthy_or_degraded_feeds(self):
        for failed in (0, 1):
            stats = {'feeds_configured': 155, 'feeds_ok': 155-failed,
                     'feeds_failed': failed, 'errors': 1}
            apply_feed_health(stats)
            self.assertEqual(run_exit_code(stats), 1)

    def test_successful_empty_feeds_are_not_an_outage(self):
        stats = {'feeds_configured': 155, 'feeds_ok': 155, 'errors': 0, 'created': 0}
        apply_feed_health(stats)
        self.assertEqual(run_exit_code(stats), 0)


if __name__ == '__main__':
    unittest.main()
