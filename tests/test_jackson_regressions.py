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

    def test_sparse_approved_notices_are_skipped_without_model_spend(self):
        examples = [BRIEF, 'Tupelo Library closes Monday for repairs.',
                    'Photos from Tupelo Library',
                    'If you plan to drink, plan for a safe ride home. A designated driver, rideshare, or taxi can make all the difference.',
                    'Please call if you know his whereabouts.', '']
        for source in examples:
            for approved in (True, False):
                with self.subTest(source=source, approved=approved):
                    client = Mock()
                    with self.assertRaisesRegex(InsufficientSource, 'source words'):
                        rewrite_article('Notice', source, 'https://example.org', client,
                                        approved_primary_source=approved)
                    client.responses.parse.assert_not_called()
