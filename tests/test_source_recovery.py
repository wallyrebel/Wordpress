import json
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from ai_rewriter import Extraction, ModelOutputError, fingerprint, rewrite_article
from pydantic import ValidationError
from database import Store
from feed_parser import fetch_feeds_with_raw
from image_handler import NewsImage
from main import IMAGE_RETRY_SECONDS, run_feed_processing, source_key
from scripts.run_report import render_report
import test_workflow


class SourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workflow.PublishingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cfg, self.entry, self.wp, self.article = (
            self.fixture.cfg, self.fixture.entry, self.fixture.wp, self.fixture.article)

    def run_flow(self, entries=None, results=None, images=None, now=10000):
        with patch('main.fetch_feeds_with_raw', return_value=entries or [(self.entry, {})]), \
             patch('main.get_source_image', side_effect=images or [NewsImage('test.jpg', 'Credit', 'https://example.org/photo.jpg')]) as image, \
             patch('main.rewrite_article', side_effect=results or [self.article]) as rewrite, \
             patch('main.time.time', return_value=now):
            stats = run_feed_processing(self.cfg, client=Mock(), wp=self.wp)
        return stats, rewrite, image

    def cached(self):
        store = Store(self.cfg.database_path)
        try:
            return store.get(source_key(self.entry))
        finally:
            store.close()

    def items(self):
        return json.loads((Path(self.cfg.review_dir) / 'run-items.json').read_text())

    def test_validation_failure_is_corrected_then_published_once(self):
        stats, rewrite, _ = self.run_flow(results=[ModelOutputError('Unsupported date'), self.article])
        self.assertEqual(stats['created'], 1)
        self.assertEqual(stats['model_attempts'], 2)
        self.assertEqual(rewrite.call_args_list[1].kwargs['correction_feedback'], 'Unsupported date')
        self.wp.upsert.assert_called_once()
        again, rewrite, image = self.run_flow()
        self.assertEqual(again['duplicates'], 1)
        rewrite.assert_not_called()
        image.assert_not_called()

    def test_two_bad_candidates_stay_held_and_reason_survives_cache(self):
        first, rewrite, _ = self.run_flow(results=[ModelOutputError('Unsupported date')] * 2)
        self.assertEqual(first['model_attempts'], 2)
        self.assertEqual(self.cached()['status'], 'held')
        # GitHub restores the DB, not the previous run's review files.
        for path in Path(self.cfg.review_dir).glob('*.json'):
            path.unlink()
        second, rewrite, _ = self.run_flow()
        rewrite.assert_not_called()
        self.wp.upsert.assert_not_called()
        self.assertEqual(second['cached_holds'], 1)
        self.assertEqual(self.items()[0]['reason'], 'Unsupported date')
        self.assertTrue(self.items()[0]['cached'])
        record = Path(self.cfg.review_dir) / (source_key(self.entry) + '.json')
        self.assertEqual(json.loads(record.read_text())['reason'], 'Unsupported date')

    def test_one_attempt_budget_resumes_correction_next_run(self):
        self.cfg.max_posts_per_run = 1
        first, _, _ = self.run_flow(results=[ModelOutputError('Unsupported time')])
        self.assertEqual(first['model_attempts'], 1)
        self.assertEqual(self.cached()['status'], 'retry_pending')
        second, rewrite, _ = self.run_flow()
        self.assertEqual(second['created'], 1)
        self.assertEqual(second['model_attempts'], 1)
        self.assertEqual(rewrite.call_args.kwargs['correction_feedback'], 'Unsupported time')

    def test_retries_cannot_bypass_durable_publication_receipt(self):
        self.cfg.max_posts_per_run = 1
        self.run_flow(results=[ModelOutputError('Unsupported time')])
        digest = fingerprint(self.entry.title, self.entry.content)
        self.wp.receipt.return_value = {'post_id': 4, 'versions': {digest: {'post_id': 4, 'status': 'publish'}}}
        stats, rewrite, image = self.run_flow()
        self.assertEqual(stats['duplicates'], 1)
        rewrite.assert_not_called()
        image.assert_not_called()
        self.wp.upsert.assert_not_called()

    def test_second_failed_attempt_next_run_exhausts_retry_allowance(self):
        self.cfg.max_posts_per_run = 1
        for attempt in range(2):
            stats, rewrite, _ = self.run_flow(results=[ModelOutputError('Unsupported claim')])
            self.assertEqual(stats['model_attempts'], 1)
            self.assertEqual(self.cached()['model_attempts'], attempt + 1)
        self.assertEqual(self.cached()['status'], 'held')
        _, rewrite, _ = self.run_flow()
        rewrite.assert_not_called()
        self.wp.upsert.assert_not_called()

    def test_malformed_structured_response_uses_bounded_validation_recovery(self):
        client = Mock()
        try:
            Extraction.model_validate({})
        except ValidationError as exc:
            client.responses.parse.side_effect = exc
        with self.assertRaisesRegex(ModelOutputError, 'required structure'):
            rewrite_article(self.entry.title, self.entry.content, self.entry.link, client)

    def test_temporary_image_failure_recovers_after_cooldown(self):
        first, rewrite, _ = self.run_flow(images=[None])
        rewrite.assert_not_called()
        self.assertEqual(self.cached()['status'], 'image_retry_pending')
        _, rewrite, image = self.run_flow(now=10001)
        rewrite.assert_not_called()
        image.assert_not_called()
        recovered, _, image = self.run_flow(now=10000 + IMAGE_RETRY_SECONDS)
        self.assertEqual(recovered['created'], 1)
        image.assert_called_once()

    def test_permanently_missing_image_stops_after_three_checks(self):
        for attempt in range(3):
            _, rewrite, image = self.run_flow(images=[None], now=10000 + attempt * IMAGE_RETRY_SECONDS)
            rewrite.assert_not_called()
            image.assert_called_once()
        self.assertEqual(self.cached()['status'], 'held')
        self.assertEqual(self.cached()['image_attempts'], 3)
        _, rewrite, image = self.run_flow(now=99999)
        rewrite.assert_not_called()
        image.assert_not_called()
        self.wp.upsert.assert_not_called()

    def test_later_feed_oldest_story_goes_first_and_all_deferrals_reported(self):
        self.cfg.max_posts_per_run = 1
        older = replace(self.entry, link='https://example.org/older',
            published=self.entry.published - timedelta(hours=1), feed_url='https://example.org/feed-two')
        later = replace(self.entry, link='https://example.org/later')
        stats, rewrite, _ = self.run_flow(entries=[(self.entry, {}), (later, {}), (older, {})])
        self.assertEqual(rewrite.call_args.args[2], older.link)
        self.assertEqual(stats['created'], 1)
        self.assertEqual(stats['deferred'], 2)
        self.assertEqual(len(self.items()), 3)
        self.assertEqual(stats['feeds'][older.feed_url]['outcomes']['publish'], 1)


class FeedReportingTests(unittest.TestCase):
    def test_feed_failure_does_not_hide_later_feed_and_empty_feeds_visible(self):
        urls = ['https://example.org/broken', 'https://example.org/empty']
        xml = b'<rss version="2.0"><channel><title>Empty source</title></channel></rss>'
        stats = {}
        with patch('feed_parser.fetch_bytes', side_effect=[TimeoutError(), (xml, 'application/rss+xml', urls[1])]):
            self.assertEqual(fetch_feeds_with_raw(urls, stats=stats), [])
        self.assertEqual(stats['feeds_failed'], 1)
        self.assertEqual(stats['feeds_ok'], 1)
        self.assertEqual(stats['feeds'][urls[0]]['error_type'], 'TimeoutError')
        self.assertEqual(stats['feeds'][urls[1]]['eligible'], 0)
        self.assertEqual(stats['feeds'][urls[1]]['publisher'], 'Empty source')

    def test_report_exposes_holds_and_escapes_source_markup(self):
        stats = {'feeds': {'https://example.org/feed': {'publisher': '<script>|Bad',
                    'status': 'ok', 'eligible': 1, 'outcomes': {'held': 1}}}, 'attention_required': 1}
        items = [{'title': '<b>Story</b>', 'source_url': 'https://example.org/story',
                  'status': 'held', 'reason': 'Unsupported date'}]
        report = render_report(stats, items)
        self.assertIn('Unsupported date', report)
        self.assertIn('https://example.org/story', report)
        self.assertIn('&lt;script&gt;&#124;Bad', report)
        self.assertNotIn('<b>Story</b>', report)
