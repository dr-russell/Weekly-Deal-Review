import base64
import json
import unittest

from control_center.config import NEWSLETTER_CONFIG
from control_center.newsletters import classify_source, extract_message_bodies, html_to_text_and_links


class NewsletterTests(unittest.TestCase):
    def test_all_configured_sources_are_recognized(self):
        config = json.loads(NEWSLETTER_CONFIG.read_text())
        for key, source in config['newsletter_sources'].items():
            matched, result = classify_source(source['match_terms'][0], '', config)
            self.assertEqual(matched, key)
            self.assertEqual(result, source)
        self.assertEqual(classify_source('Unrelated message', '', config), ('unknown', None))

    def test_nested_mime_bodies_decode_unpadded_base64(self):
        def part(kind, text):
            return {'mimeType': kind, 'body': {'data': base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')}}
        payload = {'parts': [part('text/plain', 'A deal'), {'parts': [part('text/html', '<p>A deal</p>')]}]}
        self.assertEqual(extract_message_bodies(payload), ('A deal', '<p>A deal</p>'))

    def test_html_preserves_text_and_cleans_source_links(self):
        text, links = html_to_text_and_links('<p>Acme <a href="https://example.com/?utm_source=email">announcement</a></p>')
        self.assertIn('Acme', text)
        self.assertEqual(links, [{'text': 'announcement', 'url': 'https://example.com/'}])
