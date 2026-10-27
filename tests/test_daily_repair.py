import copy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from types import SimpleNamespace
from coverage import CoverageIndex, local_publication_date
from editorial_safety import check_reader_text, event_conflict
from ai_rewriter import InsufficientSource, ModelOutputError, rewrite_article
from main import run_feed_processing
from wordpress_api import WordPressAPI
import test_workflow as fixtures

STATUSES = ['draft', 'future', 'pending', 'private', 'publish', 'trash']


def post(post_id=33889, status='publish', stamp='2026-10-27T12:00:00', body='', title='Library book sale'):
    return {'id': post_id, 'status': status, 'date_gmt': stamp,
            'title': {'raw': title}, 'content': {'raw': body}}


class CoverageTests(TestCase):
    def test_chicago_calendar_boundaries_and_dst(self):
        for utc, local in [('2026-10-27T04:59:59','2026-10-26'),
                           ('2026-10-27T05:00:00','2026-10-27'),
                           ('2026-11-02T05:59:59','2026-11-01'),
                           ('2026-11-02T06:00:00','2026-11-02')]:
            self.assertEqual(local_publication_date(post(stamp=utc)), local)

    def test_roundup_sections_and_duplicate_ids_count_once(self):
        posts = [post(body='<h2>One</h2><h2>Two</h2><h2>Three</h2>'), post(),
                 post(2, 'draft'), post(3, 'future'), post(4, 'private'),
                 post(5, stamp='2026-10-27T04:59:59')]
        index = CoverageIndex(posts, STATUSES, datetime(2026,10,27,13,tzinfo=timezone.utc))
        self.assertEqual(index.daily(5)['post_ids'], [33889])
        self.assertEqual(index.daily(5)['remaining'], 4)

    def test_all_source_links_in_other_producer_briefing_are_indexed(self):
        index = CoverageIndex([post(body='<p><a href="https://agency.gov/story/?utm_source=x">Official release</a></p>')], STATUSES)
        self.assertEqual(index.source_match('https://agency.gov/story'),33889)

    def test_missing_utc_date_fails_closed(self):
        with self.assertRaises(ValueError):
            CoverageIndex([post(stamp='')],STATUSES).daily(5)

    def test_supported_status_schema_is_mandatory(self):
        wp = WordPressAPI('https://example.org','test','test')
        wp.request = Mock(return_value={})
        with self.assertRaises(ValueError):wp.discover_statuses()
        wp.request.assert_called_once_with('OPTIONS','wp/v2/posts')

    def test_every_supported_status_is_queried_and_private_matches_block(self):
        wp = WordPressAPI('https://example.org','test','test')
        wp.supported_statuses = STATUSES
        def read(method, endpoint, **kwargs):
            status = kwargs['params']['status']
            return [post(77,'private',body='<a href="https://agency.gov/a/">Source</a>')] if status=='private' else []
        wp.request = Mock(side_effect=read)
        self.assertEqual(wp.find_source_publication('https://agency.gov/a'),77)
        self.assertTrue(all(c.args[0]=='GET' for c in wp.request.call_args_list))

    def test_lookup_error_never_becomes_absence(self):
        wp = WordPressAPI('https://example.org','test','test')
        wp.supported_statuses = STATUSES
        wp.request = Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):wp.find_source_publication('https://agency.gov/a')

    def test_fixed_external_posts_are_reconciled_without_writes(self):
        wp = WordPressAPI('https://example.org','test','test')
        wp.supported_statuses = STATUSES
        def read(method, endpoint, **kwargs):
            if endpoint == 'wp/v2/posts':return []
            return post(int(endpoint.rsplit('/',1)[1]))
        wp.request = Mock(side_effect=read)
        result = wp.reconcile(5,(33889,33891))
        self.assertEqual([p['post_id'] for p in result['anchors']],[33889,33891])
        self.assertEqual({c.kwargs['params']['status'] for c in wp.request.call_args_list if c.args[1]=='wp/v2/posts'}, set(STATUSES))
        self.assertTrue(all(c.args[0]=='GET' for c in wp.request.call_args_list))


class RepairBoundaryTests(TestCase):
    def setUp(self):
        self.f = fixtures.PublishingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_authenticated_preview_has_no_wp_or_database_writes(self):
        f=self.f
        with patch('main.fetch_feeds_with_raw',return_value=[(f.entry,{})]), \
             patch('main.get_source_image',return_value=fixtures.NewsImage('test.jpg','Credit','https://example.org/a.jpg')), \
             patch('main.rewrite_article',return_value=f.article):
            stats=run_feed_processing(f.cfg,True,client=Mock(),wp=f.wp,authenticated_preview=True)
        self.assertEqual(stats['previews'],1)
        self.assertEqual(stats['daily_after']['remaining'],5)
        self.assertFalse(Path(f.cfg.database_path).exists())
        for method in (f.wp.upsert,f.wp.taxonomy,f.wp.upload_media):method.assert_not_called()

    def test_source_in_external_briefing_blocks_before_models_or_media(self):
        f=self.f;f.wp.find_source_publication.return_value=33889
        with patch('main.fetch_feeds_with_raw',return_value=[(f.entry,{})]), patch('main.rewrite_article') as model:
            stats=run_feed_processing(f.cfg,client=Mock(),wp=f.wp)
        self.assertEqual(stats['duplicates'],1);model.assert_not_called();f.wp.upload_media.assert_not_called();f.wp.upsert.assert_not_called()

    def test_failed_reconciliation_produces_actionable_failure_without_models(self):
        f=self.f;f.wp.reconcile.side_effect=TimeoutError()
        with patch('main.rewrite_article') as model:
            stats=run_feed_processing(f.cfg,client=Mock(),wp=f.wp)
        self.assertEqual(stats['fatal_error']['stage'],'authenticated all-status coverage reconciliation')
        self.assertGreater(stats['errors'],0);model.assert_not_called();f.wp.upsert.assert_not_called()

    def test_per_run_limit_prevents_bulk_catchup_without_holding_remaining_sources(self):
        f=self.f; f.cfg.max_entries_per_feed=8
        entries=[(replace(f.entry,link=f'https://example.org/{i}'),{}) for i in range(4)]
        f.wp.upsert.side_effect=[{'post_id':10,'status':'publish'},{'post_id':11,'status':'publish'}]
        with patch('main.fetch_feeds_with_raw',return_value=entries), \
             patch('main.get_source_image',return_value=fixtures.NewsImage('test.jpg','Credit','https://example.org/a.jpg')), \
             patch('main.rewrite_article',return_value=f.article):
            stats=run_feed_processing(f.cfg,client=Mock(),wp=f.wp)
        self.assertEqual(stats['created'],2);self.assertEqual(stats['deferred'],2)
        self.assertEqual(f.wp.upsert.call_count,2)

    def test_restricted_classification_never_reaches_writer(self):
        extraction=fixtures.packet();extraction.restricted_content=True
        client=fixtures.fake_client(extraction)
        with self.assertRaisesRegex(InsufficientSource,'separate editorial approval'):
            rewrite_article('Source',fixtures.SOURCE,'https://example.org',client)
        self.assertEqual(client.responses.parse.call_count,1)

    def test_private_event_candidate_is_held_without_model_disclosure(self):
        f=self.f;f.wp.event_candidates.return_value=[post(77,'private')];client=Mock()
        self.assertIn(77,event_conflict(f.wp,f.entry,client,'gpt-6-luna')['post_ids'])
        client.responses.parse.assert_not_called()

    def test_source_lookup_error_prevents_all_writes(self):
        f=self.f;f.wp.find_source_publication.side_effect=TimeoutError()
        stats=f.run_flow()
        self.assertEqual(stats['errors'],1);f.wp.upsert.assert_not_called();f.wp.upload_media.assert_not_called()

    def test_verification_timeout_preserves_receipt_and_does_not_repeat_write(self):
        f=self.f
        with patch('publication_verification.verify_article',side_effect=TimeoutError()):
            stats=f.run_flow()
        self.assertEqual(stats['errors'],1)
        f.wp.upsert.assert_called_once()
        import json
        from main import source_key
        record=json.loads((Path(f.cfg.review_dir)/(source_key(f.entry)+'.json')).read_text())
        self.assertEqual(record['receipt']['post_id'],4)
        self.assertEqual(record['stage'],'independent public verification')
        f.wp.upsert.reset_mock()
        f.run_flow()
        f.wp.upsert.assert_not_called()


class PlaceholderTests(TestCase):
    def test_missing_information_patterns_are_rejected(self):
        for text in ['Date not provided.','The location is unknown.','Time not specified.',
                     'Venue to be announced.','TBD','[insert name]','Details were unavailable.']:
            with self.subTest(text=text),self.assertRaises(ModelOutputError):check_reader_text(text)

    def test_supported_ordinary_copy_is_accepted(self):
        check_reader_text('Tupelo Library opens its public book sale October 29 at the library.')


class PublicVerificationTests(TestCase):
    def setUp(self):
        self.article=SimpleNamespace(headline='Library sale opens Thursday',excerpt='A public library sale opens Thursday.',
            body='<p>A public library sale opens Thursday.</p><p><a href="https://agency.gov/a">Source</a></p>')
        self.url='https://example.org/library-sale/'
        self.receipt={'post_id':10,'status':'publish','url':self.url}
        self.saved={**post(10,body=self.article.body,title=self.article.headline),
                    'excerpt':{'raw':self.article.excerpt},'featured_media':0,'link':self.url}
        self.page=('<html><head><title>Library sale opens Thursday | News</title>'
                   '<meta name="description" content="A public library sale opens Thursday.">'
                   '<link rel="canonical" href="'+self.url+'"></head><body><article>'
                   '<h1>Library sale opens Thursday</h1>'+self.article.body+'</article></body></html>')
        self.wp=Mock();self.wp.request.return_value=self.saved

    def verify(self,page=None,headers=None):
        from publication_verification import verify_article
        with patch('publication_verification.public_document',return_value=((page or self.page).encode(),headers or {})):
            return verify_article(self.wp,self.receipt,self.article,self.article.body,0,None)

    def test_exact_saved_and_independent_public_article_pass(self):
        self.assertTrue(self.verify()['verified'])

    def test_wrong_saved_body_blocks_before_public_success(self):
        self.saved['content']['raw']='<p>Another article</p>'
        with self.assertRaisesRegex(ValueError,'reviewed payload'):self.verify()

    def test_wrong_description_or_canonical_is_not_a_verified_publication(self):
        for old,new in [('content="A public library sale opens Thursday."','content="Other story"'),
                        ('href="'+self.url+'"','href="https://example.org/wrong/"')]:
            with self.subTest(new=new),self.assertRaises(ValueError):self.verify(self.page.replace(old,new))

    def test_header_noindex_is_detected(self):
        with self.assertRaisesRegex(ValueError,'noindex'):self.verify(headers={'X-Robots-Tag':'noindex'})

    def test_missing_public_source_link_is_detected(self):
        with self.assertRaisesRegex(ValueError,'source link'):
            self.verify(self.page.replace('href="https://agency.gov/a"','href="https://agency.gov/wrong"'))
