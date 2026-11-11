"""Arrest and crime reports publish only from the official agency, with safeguards."""
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from ai_rewriter import (InsufficientSource, ModelOutputError, rewrite_article,
                         validate_publication_article)
from editorial_safety import (PRESUMPTION_NOTE, check_reader_text, crime_note,
                              official_crime_source, restricted_source)
from main import run_feed_processing
import test_workflow as fixtures

ARREST = 'Police arrested a Tupelo man on Tuesday and charged him with burglary. '


class OfficialSourceTests(unittest.TestCase):
    def test_only_agencies_speaking_for_themselves_are_official(self):
        for publisher in ('Tupelo Police Department on Facebook', "Lee County Sheriff's Department, Mississippi",
                          'Scott County S.O. on Facebook', "First Circuit District Attorney's Office on Facebook",
                          'Mississippi Department of Public Safety (Official) on Facebook',
                          'Mississippi Office of the State Auditor on Facebook',
                          'Crime Stoppers of Northeast Mississippi on Facebook',
                          'City of Greenville, Mississippi - Government', 'Jackson County, MS - News Flash'):
            with self.subTest(publisher=publisher):
                self.assertTrue(official_crime_source(publisher))
        for publisher in ('Magnolia Shine Podcast', 'Blake Levine Sports Reporter', 'Ole Miss Athletics',
                          'Oxford School District', 'Tupelo Community Theatre', 'Southaven Chamber of Commerce',
                          'olemisssports.com', '', None):
            with self.subTest(publisher=publisher):
                self.assertFalse(official_crime_source(publisher))

    def test_crime_text_is_held_unless_the_agency_is_the_source(self):
        self.assertTrue(restricted_source(ARREST))
        self.assertFalse(restricted_source(ARREST, crime_reports_allowed=True))
        self.assertFalse(restricted_source('The library sale opens Saturday.'))

    def test_private_sensitive_material_is_held_from_every_source(self):
        for text in ('Deputies are investigating a sexual assault.', 'The death was ruled a suicide.',
                     'The post listed her Social Security number.', 'Staff shared patient records.',
                     'He is charged with sexual battery.'):
            with self.subTest(text=text):
                self.assertTrue(restricted_source(text, crime_reports_allowed=True))
                with self.assertRaises(ModelOutputError):
                    check_reader_text(text, crime_reports_allowed=True)

    def test_reader_text_rule_follows_the_source(self):
        with self.assertRaises(ModelOutputError):
            check_reader_text(ARREST)
        check_reader_text(ARREST, crime_reports_allowed=True)


class RewriterTests(unittest.TestCase):
    def crime_packet(self):
        extraction = fixtures.packet()
        extraction.category = 'Crime & Courts'
        extraction.sensitive = True
        return extraction

    def test_unofficial_source_crime_story_stops_after_extraction(self):
        client = fixtures.fake_client(self.crime_packet())
        with self.assertRaisesRegex(InsufficientSource, 'separate editorial approval'):
            rewrite_article('Story', fixtures.SOURCE, 'https://example.org', client)
        self.assertEqual(client.responses.parse.call_count, 1)
        self.assertIn('must not be automatically published',
                      client.responses.parse.call_args_list[0].kwargs['input'][0]['content'])

    def test_official_source_crime_story_is_written_with_attribution_rules(self):
        client = fixtures.fake_client(self.crime_packet())
        article = rewrite_article('Story', fixtures.SOURCE, 'https://example.org', client,
                                  publisher='Tupelo Police Department', approved_primary_source=True,
                                  crime_reports_allowed=True)
        prompts = [call.kwargs['input'][0]['content'] for call in client.responses.parse.call_args_list]
        self.assertIn('official agency reporting its own cases', prompts[0])
        self.assertNotIn('{restricted_rule}', prompts[0])
        self.assertIn('Never state or imply', prompts[1])
        self.assertIn('states or\nimplies guilt', prompts[2])
        self.assertTrue(article.evidence['crime_reports_allowed'])
        validate_publication_article(article)
        # The saved record alone cannot waive the hold for an unofficial source.
        article.evidence['crime_reports_allowed'] = False
        with self.assertRaisesRegex(InsufficientSource, 'separate editorial approval'):
            validate_publication_article(article)

    def test_private_sensitive_flag_still_blocks_an_official_source(self):
        extraction = self.crime_packet()
        extraction.restricted_content = True
        client = fixtures.fake_client(extraction)
        with self.assertRaisesRegex(InsufficientSource, 'separate editorial approval'):
            rewrite_article('Story', fixtures.SOURCE, 'https://example.org', client,
                            crime_reports_allowed=True)
        self.assertEqual(client.responses.parse.call_count, 1)

    def test_presumption_note_is_added_to_crime_reports_only(self):
        article = rewrite_article('Sale', fixtures.SOURCE, 'https://example.org', fixtures.fake_client())
        self.assertEqual(crime_note(article), '')
        self.assertEqual(crime_note(replace(article, category='Crime & Courts')), PRESUMPTION_NOTE)
        self.assertEqual(crime_note(replace(article, body='<p>Police said a man was arrested.</p>')), PRESUMPTION_NOTE)


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PublishingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def run_flow(self, publisher):
        f = self.f
        f.entry = replace(f.entry, publisher=publisher, content=ARREST + fixtures.SOURCE)
        f.article = replace(f.article, category='Crime & Courts')
        with patch('main.fetch_feeds_with_raw', return_value=[(f.entry, {})]), \
             patch('main.get_source_image', return_value=fixtures.NewsImage('test.jpg', 'Credit', 'https://example.org/i.jpg')), \
             patch('main.rewrite_article', return_value=f.article) as rewrite:
            return run_feed_processing(f.cfg, client=Mock(), wp=f.wp), rewrite

    def test_police_report_publishes_with_the_presumption_note(self):
        stats, rewrite = self.run_flow('Tupelo Police Department on Facebook')
        self.assertEqual(stats['created'], 1)
        self.assertTrue(rewrite.call_args.kwargs['crime_reports_allowed'])
        content = self.f.wp.upsert.call_args.args[0]['content']
        self.assertIn(PRESUMPTION_NOTE + '<p class="news-source">', content)

    def test_same_report_from_an_unofficial_source_is_held_before_any_model_call(self):
        stats, rewrite = self.run_flow('Magnolia Shine Podcast')
        self.assertEqual(stats['created'], 0)
        self.assertEqual(list(stats['reasons']),
                         ['Allegation or private-sensitive source requires separate editorial approval'])
        rewrite.assert_not_called()
        self.f.wp.upsert.assert_not_called()

    def test_ordinary_story_from_police_has_no_note(self):
        f = self.f
        f.entry = replace(f.entry, publisher='Tupelo Police Department on Facebook')
        stats = f.run_flow()
        self.assertEqual(stats['created'], 1)
        self.assertFalse(f.wp.upsert.call_args.kwargs)
        self.assertNotIn('news-note', f.wp.upsert.call_args.args[0]['content'])


if __name__ == '__main__':
    unittest.main()
