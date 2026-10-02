"""Regression cases for thin-source rejection and the five-W publication contract."""
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
from ai_rewriter import (InsufficientSource, ModelOutputError, Verification,
                         rewrite_article, validate_publication_article, word_count)
from feed_parser import fetch_feeds_with_raw
from main import run_feed_processing
from wordpress_api import WordPressAPI
import test_workflow
from test_workflow import SOURCE, packet, draft, fake_client


class EditorialQualityTests(unittest.TestCase):
    def test_missing_each_w_stops_before_writer(self):
        for question in ('who', 'what', 'where', 'when', 'why'):
            extraction = packet()
            setattr(extraction.five_ws, question, [])
            client = fake_client(extraction=extraction)
            with self.subTest(question=question), self.assertRaisesRegex(InsufficientSource, question):
                rewrite_article('Story', SOURCE, 'https://example.org', client)
            self.assertEqual(client.responses.parse.call_count, 1)

    def test_approval_cannot_bypass_quality(self):
        extraction = packet()
        extraction.substantive = False
        client = fake_client(extraction=extraction)
        with self.assertRaisesRegex(InsufficientSource, 'reader value'):
            rewrite_article('Story', SOURCE, 'https://example.org', client, approved_primary_source=True)
        self.assertEqual(client.responses.parse.call_count, 1)

    def test_repeated_fact_with_new_ids_does_not_qualify(self):
        extraction = packet()
        for fact in extraction.facts:
            fact.evidence = extraction.facts[0].evidence
        with self.assertRaisesRegex(InsufficientSource, 'distinct supported facts'):
            rewrite_article('Story', SOURCE, 'https://example.org', fake_client(extraction=extraction))

    def test_source_link_headline_excerpt_cannot_satisfy_body_length(self):
        generated = draft()
        generated.paragraphs = generated.paragraphs[:1]
        client = fake_client(generated=generated)
        with self.assertRaisesRegex(InsufficientSource, 'body words'):
            rewrite_article('Story', SOURCE, 'https://example.org', client)
        self.assertEqual(client.responses.parse.call_count, 2)

    def test_padding_rejected_even_above_minimum(self):
        generated = draft()
        generated.paragraphs[2].text = generated.paragraphs[1].text
        with self.assertRaisesRegex(ModelOutputError, 'padding'):
            rewrite_article('Story', SOURCE, 'https://example.org', fake_client(generated=generated))

    def test_quality_verifier_can_reject_factually_supported_prose(self):
        verification = Verification(supported=True, issues=['Generic filler; why is missing'], quality_passed=False)
        with self.assertRaisesRegex(ModelOutputError, 'Factual verification failed'):
            rewrite_article('Story', SOURCE, 'https://example.org', fake_client(verification=verification))

    def test_false_quality_boolean_without_issues_still_rejects(self):
        verification = Verification(supported=True, issues=[], quality_passed=False)
        with self.assertRaises(ModelOutputError):
            rewrite_article('Story', SOURCE, 'https://example.org', fake_client(verification=verification))

    def test_good_article_passes_final_boundary_and_records_counts(self):
        result = rewrite_article('Story', SOURCE, 'https://example.org', fake_client())
        validate_publication_article(result)
        self.assertGreaterEqual(result.evidence['body_words'], 300)
        self.assertEqual(result.evidence['source_words'], word_count(SOURCE))
        with self.assertRaisesRegex(ModelOutputError, 'differs'):
            validate_publication_article(replace(result, body=result.body + '<p>Unverified addition.</p>'))

    def test_missing_body_w_reference_is_rejected(self):
        generated = draft()
        generated.paragraphs[0].fact_ids = ['f1', 'f2', 'f7']
        with self.assertRaisesRegex(ModelOutputError, 'when'):
            rewrite_article('Story', SOURCE, 'https://example.org', fake_client(generated=generated))

    def test_stale_publication_not_revived_by_updated_timestamp(self):
        now = datetime.now(timezone.utc)
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title><entry>'
               '<title>Old story</title><id>old</id><link href="https://example.org/old"/>'
               f'<published>{(now-timedelta(days=90)).isoformat()}</published>'
               f'<updated>{now.isoformat()}</updated></entry></feed>').encode()
        with patch('feed_parser.fetch_bytes', return_value=(xml,'application/atom+xml','https://example.org/feed')):
            self.assertEqual(fetch_feeds_with_raw(['https://example.org/feed']), [])


class PublicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workflow.PublishingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_thin_sources_do_not_starve_a_later_complete_story(self):
        f = self.fixture
        f.cfg.max_posts_per_run = 1
        thin = [replace(f.entry, link=f'https://example.org/brief-{i}', content='Thank you for coming.') for i in range(20)]
        with patch('main.fetch_feeds_with_raw', return_value=[(e,{}) for e in thin+[f.entry]]), \
             patch('main.get_source_image', return_value=Mock()) as image, \
             patch('main.rewrite_article', return_value=f.article) as rewrite:
            stats = run_feed_processing(f.cfg, client=Mock(), wp=f.wp)
        self.assertEqual(stats['skipped'], 20)
        self.assertEqual(stats['created'], 1)
        rewrite.assert_called_once()
        image.assert_called_once()

    def test_duplicate_headline_never_uploads_or_publishes(self):
        f = self.fixture
        f.wp.find_duplicate_headline.return_value = 42
        stats = f.run_flow()
        self.assertEqual(stats['duplicates'], 1)
        f.wp.taxonomy.assert_not_called()
        f.wp.upload_media.assert_not_called()
        f.wp.upsert.assert_not_called()

    def test_bad_evidence_from_other_caller_never_publishes(self):
        f = self.fixture
        f.article.evidence = {}
        stats = f.run_flow()
        self.assertEqual(stats['held'], 1)
        f.wp.upsert.assert_not_called()

    def test_duplicate_lookup_error_fails_closed(self):
        f = self.fixture
        f.wp.find_duplicate_headline.side_effect = TimeoutError()
        stats = f.run_flow()
        self.assertEqual(stats['errors'], 1)
        f.wp.upsert.assert_not_called()


class DuplicateLookupTests(unittest.TestCase):
    def test_exact_normalized_title_and_pagination(self):
        wp = WordPressAPI('https://example.org','test','test')
        wp.request = Mock(side_effect=[
            [{'id':i,'title':{'rendered':'Other story'}} for i in range(100)],
            [{'id':123,'title':{'rendered':'School &amp; library: expansion'}}]])
        self.assertEqual(wp.find_duplicate_headline('School & library expansion'), 123)
        self.assertEqual(wp.request.call_args.kwargs['params']['page'], 2)

    def test_similar_title_is_not_blocked(self):
        wp = WordPressAPI('https://example.org','test','test')
        wp.request = Mock(return_value=[{'id':123,'title':{'rendered':'Library expansion approved'}}])
        self.assertIsNone(wp.find_duplicate_headline('Library expansion delayed'))
