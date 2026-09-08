"""Regression for failed runs 11111/11112: an athletics entry redirects to video."""
import json
import warnings
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from ai_rewriter import InsufficientSource, clean_text
from config import SourcePolicy
from feed_parser import enrich_entry
from image_handler import NewsImage
from main import run_feed_processing, source_key
from database import Store
import test_workflow


class SourceRedirectTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workflow.PublishingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cfg, self.entry, self.wp, self.article = (
            self.fixture.cfg, self.fixture.entry, self.fixture.wp, self.fixture.article)

    def test_video_redirect_is_held_cached_and_does_not_block_following_article(self):
        video = replace(self.entry, link='https://example.org/highlights', content='')
        self.cfg.sources[video.feed_url] = SourcePolicy(allow_scrape=True, article_hosts=['example.org'])
        def source_response(url):
            if url == video.link:
                return b'<html>Unapproved video page text</html>', 'text/html', 'https://www.youtube.com/watch?v=example'
            return b'<article>The library announced a book sale.</article>', 'text/html', url
        with patch('main.fetch_feeds_with_raw', side_effect=lambda *args: [(replace(video), {}), (replace(self.entry), {})]), \
             patch('feed_parser.fetch_bytes', side_effect=source_response) as fetch, \
             patch('main.rewrite_article', return_value=self.article) as rewrite, \
             patch('main.get_source_image', return_value=NewsImage('test.jpg', 'Credit', 'https://example.org/image.jpg')):
            first = run_feed_processing(self.cfg, client=Mock(), wp=self.wp)
            self.assertEqual(first['errors'], 0)
            self.assertEqual(first['skipped'], 1)
            self.assertEqual(first['created'], 1)
            self.assertEqual(rewrite.call_count, 1)
            fetch.reset_mock()
            rewrite.reset_mock()
            second = run_feed_processing(self.cfg, client=Mock(), wp=self.wp)
            self.assertEqual(second['errors'], 0)
            self.assertEqual(second['created'], 0)
            self.assertEqual(second['cached_holds'], 1)
            fetch.assert_not_called()
            rewrite.assert_not_called()
        store = Store(self.cfg.database_path)
        cached = store.get(source_key(video))
        store.close()
        self.assertEqual(cached['status'], 'held')
        self.assertIn('outside approved article hosts', cached['reason'])
        self.assertEqual(cached['model_attempts'], 0)

    def test_non_html_media_is_an_explained_source_hold(self):
        with patch('feed_parser.fetch_bytes', return_value=(b'%PDF', 'application/pdf', self.entry.link)):
            with self.assertRaisesRegex(InsufficientSource, 'not an HTML article'):
                enrich_entry(self.entry, SourcePolicy(allow_scrape=True, article_hosts=['example.org']))

    def test_unexpected_value_error_still_fails_and_identifies_stage(self):
        with patch('main.fetch_feeds_with_raw', return_value=[(self.entry, {})]), \
             patch('main.enrich_entry', side_effect=ValueError('private diagnostic')):
            stats = run_feed_processing(self.cfg, client=Mock(), wp=self.wp)
        self.assertEqual(stats['errors'], 1)
        self.wp.upsert.assert_not_called()
        record = json.loads((Path(self.cfg.review_dir)/(source_key(self.entry)+'.json')).read_text())
        self.assertEqual(record['stage'], 'source article enrichment')
        self.assertNotIn('private diagnostic', json.dumps(record))

    def test_url_only_text_does_not_emit_html_parser_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            self.assertEqual(clean_text('https://example.org/?a=1&amp;b=2'), 'https://example.org/?a=1&b=2')
        self.assertFalse(caught)
