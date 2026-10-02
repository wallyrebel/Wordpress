import copy
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pydantic import ValidationError
from ai_rewriter import (Extraction, Verification, InsufficientSource, ModelOutputError,
    rewrite_article, validate_publication_article, editorial_format)
from ai_rewriter import numeric_tokens
from roundups import (RoundupExtraction, ExtractedSection, RoundupDraft, WrittenSection,
    rewrite_roundup, validate_sections)
from roundups import candidate_group
from main import run_feed_processing, run_roundup, source_key
from wordpress_api import WordPressAPI
import test_workflow as fixtures


def bundle():
    sources, packets, drafts = [], [], []
    for i, city in enumerate(['Tupelo','Oxford','Corinth']):
        text = (fixtures.SOURCE.split(fixtures.DETAILS[1])[0]).strip().replace('Tupelo',city)
        p = fixtures.packet()
        p.entities = [city+' Library',city]
        p.facts = [f for f in p.facts if f.id in ('f1','f2','f3','f7','f8')]
        for f in p.facts:
            f.evidence = f.evidence.replace('Tupelo',city)
            f.statement = f.statement.replace('Tupelo',city)
        d = fixtures.draft()
        d.headline = city+' Library plans book sale'
        d.excerpt = city+' Library announces a book sale.'
        d.paragraphs = d.paragraphs[:2]
        d.paragraphs[0].text = text.split(fixtures.DETAILS[0])[0].strip()
        sid = 'source'+str(i)
        sources.append({'source_id':sid,'url':f'https://example.org/{city}',
            'title':d.headline,'publisher':city+' Library','source_date':'2026-09-11T12:00:00Z','text':text})
        packets.append(ExtractedSection(source_id=sid,extraction=p))
        drafts.append(WrittenSection(source_id=sid,draft=d))
    return sources, RoundupExtraction(sections=packets), RoundupDraft(sections=drafts)


def roundup_client(extracted, drafted, verification=None):
    client = Mock()
    client.responses.parse.side_effect = [SimpleNamespace(status='completed',usage=None,output_parsed=v)
        for v in (extracted,drafted,verification or Verification(supported=True,issues=[],quality_passed=True))]
    return client


class BalancedCoverageTests(unittest.TestCase):
    def test_sensitive_sources_cannot_displace_roundup_coverage(self):
        entry = SimpleNamespace(title='Community visit',content='Useful details',publisher='County Sheriff')
        policy = SimpleNamespace(category='Mississippi News',publisher='')
        self.assertIsNone(candidate_group(entry,policy))
        entry.publisher = 'College Athletics'
        entry.content = 'The volleyball tournament is on October 3.'
        self.assertEqual(candidate_group(entry,policy),'sports')
        entry.content = 'The library sale is on October 3.'
        self.assertEqual(candidate_group(entry,policy),'community')

    def test_ordinal_calendar_day_can_be_written_in_ap_style(self):
        self.assertEqual(numeric_tokens('October 1st and 22nd'),numeric_tokens('Oct. 1 and 22'))
        self.assertNotEqual(numeric_tokens('October 1st'),numeric_tokens('Oct. 2'))

    def test_calendar_formats_preserve_both_month_and_day(self):
        self.assertEqual(numeric_tokens('10/08'), numeric_tokens('Oct. 8th'))
        self.assertEqual(numeric_tokens('October 8, 2026'), numeric_tokens('10/8, 2026'))
        self.assertNotEqual(numeric_tokens('10/8'), numeric_tokens('Nov. 8'))
        self.assertNotEqual(numeric_tokens('10/8'), numeric_tokens('Oct. 9'))
        self.assertEqual(numeric_tokens('601-555-0108; 10:08am'), {'601-555-0108','10:08'})
        self.assertEqual(numeric_tokens('October 26-November 1'), numeric_tokens('Oct. 26 and Nov. 1'))
        self.assertEqual(numeric_tokens('10/26-11/1'), numeric_tokens('Oct. 26 through Nov. 1'))
        self.assertNotEqual(numeric_tokens('10/26-11/1'), numeric_tokens('Oct. 25 through Nov. 1'))

    def test_shared_meridiem_time_ranges_allow_ap_style(self):
        self.assertEqual(numeric_tokens('6:00–10:00 PM'),numeric_tokens('6 p.m. to 10 p.m.'))
        self.assertNotEqual(numeric_tokens('6:30–10:00 PM'),numeric_tokens('6 p.m. to 10 p.m.'))
        self.assertNotEqual(numeric_tokens('6:00–10:00 PM'),numeric_tokens('7 p.m. to 10 p.m.'))
        self.assertEqual(numeric_tokens('A 6-10 score'),{'6-10'})

    def test_source_names_cannot_replace_fact_ids_in_model_schema(self):
        data = fixtures.packet().model_dump()
        data['five_ws']['who'] = ['Mississippi State', 'South Carolina']
        with self.assertRaises(ValidationError):
            Extraction.model_validate(data)
        schema = Extraction.model_json_schema()
        self.assertEqual(schema['$defs']['FiveWs']['properties']['who']['items']['pattern'], '^f[1-9][0-9]*$')
        # Even a well-formed ID must refer to an existing fact before publication.
        p = fixtures.packet()
        p.five_ws.who = ['f999']
        from ai_rewriter import check_completeness
        with self.assertRaisesRegex(ModelOutputError,'Unknown five-W'):
            check_completeness(p)

    def test_verified_brief_publishes_without_a_photo(self):
        fixture = fixtures.PublishingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        f = fixture
        f.entry = replace(f.entry, content='Road closure. '+fixtures.SOURCE)
        p = fixtures.packet()
        p.public_service = True
        d = fixtures.draft()
        d.paragraphs = d.paragraphs[:2]
        f.article = rewrite_article('Notice',fixtures.SOURCE,f.entry.link,
            fixtures.fake_client(extraction=p,generated=d),format='brief')
        stats = f.run_flow(image=False)
        self.assertEqual(stats['created'],1)
        self.assertEqual(f.wp.upsert.call_args.args[0]['featured_media'],0)
        f.wp.upload_media.assert_not_called()

    def test_actionable_brief_does_not_need_an_invented_why(self):
        p = fixtures.packet()
        p.public_service = True
        p.five_ws.why = []
        d = fixtures.draft()
        d.paragraphs = d.paragraphs[:2]
        result = rewrite_article('Notice',fixtures.SOURCE,'https://example.org',
            fixtures.fake_client(extraction=p,generated=d),format='brief')
        validate_publication_article(result)
        self.assertLess(result.evidence['body_words'],300)
        self.assertGreaterEqual(result.evidence['body_words'],70)

    def test_short_promotional_item_cannot_claim_brief_exception(self):
        with self.assertRaisesRegex(InsufficientSource,'public-service'):
            rewrite_article('Notice',fixtures.SOURCE,'https://example.org',fixtures.fake_client(),format='brief')

    def test_brief_still_requires_when_where_and_verified_claims(self):
        p = fixtures.packet()
        p.public_service = True
        p.five_ws.when = []
        with self.assertRaisesRegex(InsufficientSource,'when'):
            rewrite_article('Notice',fixtures.SOURCE,'https://example.org',fixtures.fake_client(extraction=p),format='brief')

    def test_three_source_roundup_is_valid_and_each_section_has_its_link(self):
        sources, p, d = bundle()
        article = rewrite_roundup(sources,roundup_client(p,d),'nano','writer')
        validate_publication_article(article)
        self.assertEqual(article.body.count('<h2>'),3)
        self.assertEqual(article.body.count('class="news-source"'),3)
        for s in sources:
            self.assertIn(s['url'],article.body)
        with self.assertRaisesRegex(ModelOutputError,'differs'):
            validate_publication_article(replace(article,body=article.body+'<p>Extra claim.</p>'))

    def test_concise_complete_section_can_join_substantial_briefing(self):
        from ai_rewriter import Paragraph, word_count
        sources,p,d = bundle()
        concise = ("Tupelo Library will hold a public book sale in Tupelo, Mississippi, on September 12 "
                   "at 10 a.m. Proceeds support the community reading program and replacement of worn "
                   "materials in the children's collection.")
        self.assertLess(word_count(concise),45)
        d.sections[0].draft.paragraphs = [Paragraph(text=concise,fact_ids=['f1','f2','f3','f7','f8'])]
        self.assertGreaterEqual(validate_sections(sources,p,d),250)
        # Three short sections alone still cannot pass the whole-article floor.
        for section in d.sections[1:]:
            city = 'Oxford' if section.source_id == 'source1' else 'Corinth'
            section.draft.paragraphs = [Paragraph(text=concise.replace('Tupelo',city),fact_ids=['f1','f2','f3','f7','f8'])]
        with self.assertRaisesRegex(InsufficientSource,'250-900'):
            validate_sections(sources,p,d)

    def test_briefing_date_uses_mississippi_calendar_not_utc(self):
        sources,p,d = bundle()
        client = roundup_client(p,d)
        with patch('roundups.datetime') as clock:
            clock.now.return_value = datetime(2026,10,3,1,0,tzinfo=timezone.utc)
            article = rewrite_roundup(sources,client,'nano','writer')
        self.assertTrue(article.headline.endswith('October 2, 2026'))
        import json
        for call in client.responses.parse.call_args_list:
            payload = json.loads(call.kwargs['input'][1]['content'])
            self.assertEqual(payload['compiled_at'],'2026-10-02T20:00:00-05:00')

    def test_roundup_cannot_borrow_evidence_from_another_source(self):
        sources,p,d = bundle()
        p.sections[0].extraction.facts[0].evidence = p.sections[1].extraction.facts[0].evidence
        with self.assertRaisesRegex(InsufficientSource,'own source'):
            rewrite_roundup(sources,roundup_client(p,d),'nano','writer')

    def test_invalid_fourth_section_does_not_suppress_three_valid_items(self):
        for failure in ('unknown_fact', 'invented_evidence'):
            with self.subTest(failure=failure):
                sources,p,d = bundle()
                bad = copy.deepcopy(p.sections[0])
                bad.source_id = 'rejected'
                if failure == 'unknown_fact':
                    bad.extraction.five_ws.when = ['invented-id']
                else:
                    bad.extraction.facts[0].evidence = 'This evidence is absent from the source.'
                p.sections.append(bad)
                sources.append({**sources[0], 'source_id':'rejected','url':'https://example.org/rejected'})
                client = roundup_client(p,d)
                article = rewrite_roundup(sources,client,'nano','writer')
                validate_publication_article(article)
                self.assertEqual(len(article.evidence['sources']),3)
                self.assertNotIn('https://example.org/rejected',article.body)
                import json
                payload = json.loads(client.responses.parse.call_args_list[1].kwargs['input'][1]['content'])
                self.assertNotIn('rejected',[s['source_id'] for s in payload['sources']])

    def test_roundup_cannot_borrow_numbers_from_another_source(self):
        sources,p,d = bundle()
        sources[1]['text'] += ' There are 765 books.'
        d.sections[0].draft.paragraphs[0].text += ' There are 765 books.'
        with self.assertRaisesRegex(ModelOutputError,'assigned source'):
            validate_sections(sources,p,d)

    def test_duplicate_source_sections_do_not_make_a_roundup(self):
        sources,p,d = bundle()
        d.sections[1].source_id = d.sections[0].source_id
        with self.assertRaises(InsufficientSource):
            validate_sections(sources,p,d)

    def test_sensitive_source_is_excluded_from_general_roundup(self):
        sources,p,d = bundle()
        p.sections[0].extraction.sensitive = True
        with self.assertRaisesRegex(InsufficientSource,'three'):
            rewrite_roundup(sources,roundup_client(p,d),'nano','writer')

    def test_independent_verifier_can_reject_cross_story_context(self):
        sources,p,d = bundle()
        v = Verification(supported=False,quality_passed=False,issues=['Wrong date assigned to Oxford'])
        with self.assertRaisesRegex(ModelOutputError,'Wrong date'):
            rewrite_roundup(sources,roundup_client(p,d,v),'nano','writer')

    def test_source_recovery_uses_exact_attribution_not_incidental_link(self):
        wp = WordPressAPI('https://example.org','test','test')
        wp.request = Mock(return_value=[
            {'id':1,'content':{'rendered':'<a href="https://example.org/source">Related</a>'}},
            {'id':2,'content':{'rendered':'<p class="news-source"><a href="https://example.org/source">Source</a></p>'}}])
        self.assertEqual(wp.find_source_publication('https://example.org/source'),2)
        self.assertIsNone(wp.find_source_publication('https://example.org/different'))

    def test_alerts_are_processed_before_routine_articles(self):
        fixture = fixtures.PublishingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        f = fixture
        f.cfg.max_posts_per_run = 1
        alert = replace(f.entry,link='https://example.org/alert',content='Road closure. '+fixtures.SOURCE)
        f.article.evidence['extraction']['public_service'] = True
        with patch('main.fetch_feeds_with_raw',return_value=[(f.entry,{}),(alert,{})]), \
             patch('main.get_source_image',return_value=Mock()), \
             patch('main.rewrite_article',return_value=f.article) as rewrite:
            stats = run_feed_processing(f.cfg,client=Mock(),wp=f.wp)
        self.assertEqual(rewrite.call_args.args[2],alert.link)
        self.assertEqual(rewrite.call_args.kwargs['format'],'brief')
        self.assertEqual(stats['created'],1)

    def test_roundup_publication_adopts_each_included_source(self):
        fixture = fixtures.PublishingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        f = fixture
        sources,p,d = bundle()
        candidates = []
        for i,s in enumerate(sources):
            entry = replace(f.entry,link=s['url'],content=s['text'],feed_url=s['url']+'/feed')
            policy = replace(f.cfg.policy(f.entry.feed_url),publisher=s['publisher'])
            sid = source_key(entry)
            s['source_id'] = sid
            p.sections[i].source_id = sid
            d.sections[i].source_id = sid
            candidates.append((entry,{},policy,'feed-hash','digest'))
        article = rewrite_roundup(sources,roundup_client(p,d),'nano','writer')
        f.wp.find_source_publication.return_value = None
        f.wp.upsert.return_value = {'post_id':42,'status':'publish'}
        stats = {'created':0,'errors':0,'duplicates':0,'model_attempts':0,'model_attempt_budget':1,'reasons':{}}
        store = Mock()
        import time
        with patch('main.rewrite_roundup',return_value=article), patch('main.get_source_image',return_value=Mock()):
            run_roundup(f.cfg,candidates,stats,time.monotonic(),False,Mock(),f.wp,store,Mock())
        self.assertEqual(stats['created'],1)
        self.assertEqual(f.wp.upsert.call_count,4)
        self.assertEqual(store.save.call_count,3)
        self.assertEqual([c.args[0]['adopt_post_id'] for c in f.wp.upsert.call_args_list[1:]],[42,42,42])
