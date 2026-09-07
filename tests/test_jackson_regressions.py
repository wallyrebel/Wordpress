import unittest
import json
from unittest.mock import Mock

from ai_rewriter import (Draft, Extraction, Fact, InsufficientSource, ModelOutputError,
                         Paragraph, Verification, numeric_tokens, rewrite_article,
                         normalize_quote_stops, check_direct_quotes)
from test_workflow import SOURCE, draft, fake_client

BRIEF = ("The Jackson Police Department is currently conducting a homicide investigation "
         "on Langley at Robinson Road.. update to follow")


class JacksonRegressions(unittest.TestCase):
    def test_added_sentence_period_moves_outside_exact_quotation(self):
        source = 'Drive Sober or Get Pulled Over'
        for original, expected in [
                ('Police said, "Drive Sober or Get Pulled Over."', 'Police said, "Drive Sober or Get Pulled Over".'),
                ('Police said, “Drive Sober or Get Pulled Over.”', 'Police said, “Drive Sober or Get Pulled Over”.')]:
            normalized = normalize_quote_stops(original, source)
            self.assertEqual(normalized, expected)
            check_direct_quotes(normalized, source)
        altered = 'Police said, "Drive Sober or Get Arrested."'
        self.assertEqual(normalize_quote_stops(altered, source), altered)
        with self.assertRaises(ModelOutputError):
            check_direct_quotes(altered, source)
        exact = 'Police said, "Drive Sober or Get Pulled Over."'
        self.assertEqual(normalize_quote_stops(exact, source + '.'), exact)

    def test_attached_meridiem_keeps_minutes(self):
        for original in ('1:56am', '1:56AM', '1:56a.m.', '1:56 am'):
            self.assertEqual(numeric_tokens(original), {'1:56'})
            self.assertEqual(numeric_tokens(original), numeric_tokens('1:56 a.m.'))
        self.assertEqual(numeric_tokens('10:00pm'), numeric_tokens('10 p.m.'))
        self.assertNotEqual(numeric_tokens('1:56am'), numeric_tokens('1:57 a.m.'))
        self.assertNotEqual(numeric_tokens('1:56am'), numeric_tokens('2:56 a.m.'))
        self.assertEqual(numeric_tokens('56 years old; 601-960-1800'), {'56', '601-960-1800'})

    def test_formatted_time_reaches_verifier_but_changed_minutes_do_not(self):
        source = SOURCE.replace('10 a.m.', '1:56am')
        generated = draft()
        generated.paragraphs[0].text = 'Tupelo Library plans a book sale at 1:56 a.m.'
        client = fake_client(generated=generated)
        rewrite_article('Sale', source, 'https://example.org', client)
        self.assertEqual(client.responses.parse.call_count, 3)
        generated.paragraphs[0].text = 'Tupelo Library plans a book sale at 1:57 a.m.'
        client = fake_client(generated=generated)
        with self.assertRaisesRegex(ModelOutputError, 'Numeric tokens absent from source: 1:57'):
            rewrite_article('Sale', source, 'https://example.org', client)
        self.assertEqual(client.responses.parse.call_count, 2)

    def brief_client(self, verification=None):
        extraction = Extraction(mississippi_relevant=True, sensitive=True,
            category='Crime & Courts', entities=['Jackson Police Department', 'Robinson Road'],
            facts=[Fact(id='f1', statement='Police are investigating a homicide at the stated location.',
                        evidence=BRIEF)])
        draft = Draft(headline='Jackson police investigate homicide at Langley and Robinson Road',
            headline_fact_ids=['f1'], excerpt='Jackson police say a homicide investigation is underway.',
            paragraphs=[Paragraph(text='Jackson police said they are investigating a homicide at Langley and Robinson Road.',
                                  fact_ids=['f1'])])
        return fake_client(extraction, draft, verification)

    def test_approved_short_brief_gets_full_factual_verification(self):
        self.assertLess(len(BRIEF.split()), 20)
        client = self.brief_client()
        article = rewrite_article('Photos from Jackson Police Department', BRIEF,
            'https://www.facebook.com/1182108394098308/posts/1381165100859302', client,
            publisher='Jackson Police Department', source_date='2026-09-07T00:49:23+00:00',
            approved_primary_source=True)
        self.assertFalse(article.requires_review)
        self.assertTrue(article.tags)
        self.assertEqual(client.responses.parse.call_count, 3)
        self.assertEqual(client.responses.parse.call_args.kwargs['reasoning']['effort'], 'medium')
        writer_payload = json.loads(client.responses.parse.call_args_list[1].kwargs['input'][1]['content'])
        self.assertNotIn('source_date', writer_payload)

    def test_short_brief_with_unsupported_claim_still_rejected(self):
        client = self.brief_client(Verification(supported=False, issues=['Unsupported victim detail']))
        with self.assertRaisesRegex(ModelOutputError, 'Factual verification failed'):
            rewrite_article('Police update', BRIEF, 'https://example.org', client,
                            approved_primary_source=True)

    def test_unapproved_short_source_and_empty_source_still_rejected(self):
        for text, approved in ((BRIEF, False), ('', True)):
            client = Mock()
            with self.assertRaises(InsufficientSource):
                rewrite_article('Headline', text, 'https://example.org', client,
                                approved_primary_source=approved)
            client.responses.parse.assert_not_called()

    def test_very_short_factual_notice_can_publish_but_placeholder_cannot(self):
        source = 'Tupelo Library closes Monday for repairs.'
        extraction = Extraction(mississippi_relevant=True, sensitive=False,
            category='Community', entities=['Tupelo Library'],
            facts=[Fact(id='f1', statement=source, evidence=source)])
        generated = Draft(headline='Tupelo Library closes Monday for repairs',
            headline_fact_ids=['f1'], excerpt=source,
            paragraphs=[Paragraph(text=source, fact_ids=['f1'])])
        client = fake_client(extraction, generated)
        article = rewrite_article('Notice', source, 'https://example.org', client,
                                  approved_primary_source=True)
        self.assertFalse(article.requires_review)
        self.assertEqual(client.responses.parse.call_count, 3)
        extraction.facts = []
        client = fake_client(extraction)
        with self.assertRaisesRegex(InsufficientSource, 'Missing central facts'):
            rewrite_article('Photos', 'Photos from Tupelo Library', 'https://example.org',
                            client, approved_primary_source=True)
        self.assertEqual(client.responses.parse.call_count, 1)

    def test_first_person_advisory_uses_known_publisher_when_text_has_no_names(self):
        source = 'If you plan to drink, plan for a safe ride home. A designated driver, rideshare, or taxi can make all the difference.'
        extraction = Extraction(mississippi_relevant=True, sensitive=False,
            category='Community', entities=[], facts=[Fact(id='f1', statement='The publisher advises arranging a safe ride.', evidence=source)])
        generated = Draft(headline='Hattiesburg police urge safe rides home',
            headline_fact_ids=['f1'], excerpt='Hattiesburg police advise arranging a safe ride after drinking.',
            paragraphs=[Paragraph(text='Hattiesburg police advised people who plan to drink to arrange a safe ride home with a designated driver, rideshare or taxi.', fact_ids=['f1'])])
        result = rewrite_article('Safety reminder', source, 'https://example.org',
            fake_client(extraction, generated), approved_primary_source=True,
            publisher='Hattiesburg Police Department (Official) on Facebook')
        self.assertEqual(result.tags, ['hattiesburg police department (official)'])
        self.assertFalse(result.requires_review)
        with self.assertRaises(InsufficientSource):
            rewrite_article('Safety reminder', source, 'https://example.org', fake_client(extraction, generated))
        extraction.facts = []
        with self.assertRaisesRegex(InsufficientSource, 'Missing central facts'):
            rewrite_article('Vague request', 'Please call if you know his whereabouts.', 'https://example.org',
                fake_client(extraction), approved_primary_source=True, publisher='Police department')
