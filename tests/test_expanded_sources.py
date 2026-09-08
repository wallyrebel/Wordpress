import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from config import SourcePolicy
from ai_rewriter import InsufficientSource
from feed_parser import enrich_entry, fetch_feeds_with_raw
from image_handler import NewsImage, image_candidates
from main import run_feed_processing, source_key
from database import Store
import test_workflow


class ExpandedSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workflow.PublishingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cfg, self.entry, self.wp, self.article = (
            self.fixture.cfg, self.fixture.entry, self.fixture.wp, self.fixture.article)

    def test_all_sources_read_even_with_one_item_budget_and_one_failure(self):
        urls = [f'https://example.org/feed-{i}' for i in range(133)]
        xml = b'<rss version="2.0"><channel><title>Source</title></channel></rss>'
        def response(url):
            if url == urls[10]:
                raise TimeoutError()
            return xml, 'application/rss+xml', url
        stats = {}
        with patch('feed_parser.fetch_bytes', side_effect=response) as read:
            fetch_feeds_with_raw(urls, max_entries_per_feed=1, stats=stats)
        self.assertEqual({c.args[0] for c in read.call_args_list}, set(urls))
        self.assertEqual(stats['feeds_ok'], 132)
        self.assertEqual(stats['feeds_failed'], 1)
        self.assertEqual(len(stats['feeds']), 133)

    def test_html_error_page_is_a_failed_feed_not_an_empty_success(self):
        with patch('feed_parser.fetch_bytes', return_value=(b'<html>Unavailable</html>', 'text/html', 'https://example.org/feed')):
            stats = {}
            fetch_feeds_with_raw(['https://example.org/feed'], stats=stats)
        self.assertEqual(stats['feeds_failed'], 1)

    def test_transient_feed_timeout_retries_only_that_feed_and_reports_recovery(self):
        urls = ['https://example.org/slow', 'https://example.org/healthy']
        calls = []
        xml = b'<rss version="2.0"><channel><title>Source</title></channel></rss>'
        def response(url):
            calls.append(url)
            if url == urls[0] and calls.count(url) == 1:
                raise TimeoutError()
            return xml, 'application/rss+xml', url
        stats = {}
        with patch('feed_parser.fetch_bytes', side_effect=response):
            fetch_feeds_with_raw(urls, stats=stats)
        self.assertEqual(calls.count(urls[0]), 2)
        self.assertEqual(calls.count(urls[1]), 1)
        self.assertEqual(stats['feeds_ok'], 2)
        self.assertEqual(stats.get('feeds_failed', 0), 0)
        self.assertEqual(stats['feeds_retried'], 1)
        self.assertEqual(stats['feeds_recovered'], 1)
        self.assertTrue(stats['feeds'][urls[0]]['recovered_on_retry'])

    def test_persistent_feed_timeout_remains_visible_as_a_failure(self):
        stats = {}
        with patch('feed_parser.fetch_bytes', side_effect=TimeoutError()) as fetch:
            fetch_feeds_with_raw(['https://example.org/slow'], stats=stats)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(stats['feeds_failed'], 1)
        self.assertEqual(stats.get('feeds_recovered', 0), 0)
        self.assertEqual(stats['feeds']['https://example.org/slow']['read_attempts'], 2)

    def test_dedicated_sports_feed_keeps_sports_taxonomy_when_model_chooses_community(self):
        self.cfg.sources[self.entry.feed_url] = SourcePolicy(category='Sports')
        self.fixture.run_flow()
        self.assertEqual(self.wp.taxonomy.call_args.args[0], 'Sports')
        record = json.loads((Path(self.cfg.review_dir)/(source_key(self.entry)+'.json')).read_text())
        self.assertEqual(record['article']['category'], 'Sports')

    def test_general_source_retains_model_category(self):
        self.fixture.run_flow()
        self.assertEqual(self.wp.taxonomy.call_args.args[0], self.article.category)

    def test_civic_alert_title_only_feed_uses_report_without_related_stories(self):
        html = ('<html><head><meta property="og:image" content="/sheriff.jpg"></head>'
                '<body><h1>Crime report</h1><div class="article-content redesign-text fr-view">'
                '<p>The sheriff reported three vehicle thefts over the holiday weekend.</p></div>'
                '<div class="related-articles">Unrelated old murder investigation</div></body></html>')
        entry = replace(self.entry, content='')
        raw = {}
        with patch('feed_parser.fetch_bytes', return_value=(html.encode(), 'text/html', entry.link)):
            result = enrich_entry(entry, SourcePolicy(allow_scrape=True, article_hosts=['example.org']), raw)
        self.assertIn('three vehicle thefts', result.content)
        self.assertNotIn('murder', result.content)
        self.assertEqual(raw['article_images'], ['https://example.org/sheriff.jpg'])

    def test_wordpress_uses_own_featured_photo_when_social_preview_is_tiny(self):
        html = ('<html><head><meta property="og:image" content="/tiny.jpg"></head>'
                '<body><article><img class="wp-post-image" src="/photo-300.jpg" '
                'srcset="/photo-300.jpg 300w, /photo-1024.jpg 1024w">'
                '<div class="entry-content">The department announced the result.</div></article>'
                '<aside><img src="/unrelated.jpg"></aside></body></html>')
        raw = {}
        with patch('feed_parser.fetch_bytes', return_value=(html.encode(), 'text/html', self.entry.link)):
            enrich_entry(self.entry, SourcePolicy(allow_scrape=True, article_hosts=['example.org']), raw)
        self.assertEqual(image_candidates(raw)[0], 'https://example.org/photo-1024.jpg')
        self.assertNotIn('https://example.org/unrelated.jpg', image_candidates(raw))

    def test_full_story_survives_outer_aspnet_form_and_uses_source_featured_image(self):
        body = '<p>The college won its opening game. The coach announced the result.</p>' * 15
        html = ('<html><head><meta property="og:image" content="/images/game.jpg"></head>'
                '<body><form><nav>Tickets and merchandise</nav><article>'
                '<div class="sidearm-story-template-text">' + body + '</div>'
                '<aside>Related unrelated story<img src="/unrelated.jpg"></aside></article></form></body></html>')
        policy = SourcePolicy(allow_scrape=True, article_hosts=['example.org'])
        raw = {'media_thumbnail': [{'url': 'https://example.org/tiny.jpg'}]}
        entry = replace(self.entry, content='Short excerpt.')
        with patch('feed_parser.fetch_bytes', return_value=(html.encode(), 'text/html', entry.link)):
            result = enrich_entry(entry, policy, raw)
        self.assertGreater(len(result.content), 500)
        self.assertNotIn('Tickets', result.content)
        self.assertNotIn('unrelated', result.content)
        self.assertEqual(image_candidates(raw)[0], 'https://example.org/images/game.jpg')
        self.assertNotIn('https://example.org/unrelated.jpg', image_candidates(raw))

    def test_new_athletics_template_expands_even_a_long_rss_excerpt(self):
        text = 'The college announced a complete match report. ' * 30
        html = '<div id="storyPageContentBody">' + text + '</div>'
        entry = replace(self.entry, content='An excerpt with incomplete match details. ' * 15)
        policy = SourcePolicy(allow_scrape=True, article_hosts=['example.org'])
        with patch('feed_parser.fetch_bytes', return_value=(html.encode(), 'text/html', entry.link)):
            result = enrich_entry(entry, policy)
        self.assertEqual(result.content, text.strip())

    def test_source_page_enrichment_requires_explicit_host_permission(self):
        with patch('feed_parser.fetch_bytes') as fetch:
            enrich_entry(self.entry, SourcePolicy(allow_scrape=True, article_hosts=['other.example']))
        fetch.assert_not_called()
        with patch('feed_parser.fetch_bytes', return_value=(b'<article>Bad redirect</article>', 'text/html', 'https://other.example/story')):
            with self.assertRaisesRegex(InsufficientSource, 'outside approved article hosts'):
                enrich_entry(self.entry, SourcePolicy(allow_scrape=True, article_hosts=['example.org']))

    def test_time_limit_defers_without_permanent_hold_then_publishes_next_run(self):
        self.cfg.max_run_seconds = 600
        with patch('main.fetch_feeds_with_raw', return_value=[(self.entry, {})]), \
             patch('main.time.monotonic', side_effect=[0, 601, 602]), \
             patch('main.rewrite_article') as rewrite, patch('main.get_source_image') as image:
            stats = run_feed_processing(self.cfg, client=Mock(), wp=self.wp)
        self.assertEqual(stats['deferred'], 1)
        self.assertTrue(stats['time_budget_reached'])
        rewrite.assert_not_called()
        image.assert_not_called()
        store = Store(self.cfg.database_path)
        self.assertIsNone(store.get(source_key(self.entry)))
        store.close()
        with patch('main.fetch_feeds_with_raw', return_value=[(self.entry, {})]), \
             patch('main.time.monotonic', return_value=0), \
             patch('main.rewrite_article', return_value=self.article), \
             patch('main.get_source_image', return_value=NewsImage('test.jpg', 'Credit', 'https://example.org/image.jpg')):
            resumed = run_feed_processing(self.cfg, client=Mock(), wp=self.wp)
        self.assertEqual(resumed['created'], 1)

    def test_more_than_ten_stories_publish_and_remaining_work_is_reported(self):
        self.cfg.max_posts_per_run = 30
        entries = [(replace(self.entry, link=f'https://example.org/story-{i}',
                    feed_url=f'https://example.org/feed-{i}'), {}) for i in range(35)]
        with patch('main.fetch_feeds_with_raw', return_value=entries), \
             patch('main.rewrite_article', return_value=self.article), \
             patch('main.get_source_image', return_value=NewsImage('test.jpg', 'Credit', 'https://example.org/image.jpg')):
            stats = run_feed_processing(self.cfg, client=Mock(), wp=self.wp)
        self.assertEqual(stats['created'], 30)
        self.assertEqual(stats['deferred'], 5)
        self.assertEqual(len(json.loads((Path(self.cfg.review_dir)/'run-items.json').read_text())), 35)
