"""Synthetic captive redirect forms; no real network or credentials."""

import unittest
import urllib.parse

from core import auth


class DiscoveryTests(unittest.TestCase):
    query = "mac=00%3A11&wlanacname=Example-AC&wlanuserip=192.0.2.44&copy=token&notin=fixture"

    def test_redirect_url_preserves_raw_encoding_and_entity_like_field_names(self):
        self.assertEqual(auth._extract_query_string_from_url(
            "http://portal.example.org/eportal/index.jsp?" + self.query), self.query)

    def test_js_location_preserves_parameter_order_and_raw_mac(self):
        page = "<script>location.href='http://portal.example.org/eportal/index.jsp?" + self.query + "';</script>"
        self.assertEqual(auth._extract_query_string_from_html(page), self.query)
        self.assertEqual(auth._portal_mac(self.query), "00%3A11")

    def test_html_ampersand_entities_do_not_corrupt_other_query_fields(self):
        for entity in ("&amp;", "&#38;", "&#x26;"):
            with self.subTest(entity=entity):
                page = '<meta http-equiv="refresh" content="0;url=/eportal/index.jsp?' + self.query.replace("&", entity) + '">'
                self.assertEqual(auth._extract_query_string_from_html(page), self.query)

    def test_js_escaped_url_and_querystring_assignment_are_supported(self):
        page = r"<script>var queryString='" + self.query.replace("&", r"\u0026") + "';</script>"
        self.assertEqual(auth._extract_query_string_from_html(page), self.query)

    def test_whole_encoded_query_is_unwrapped_without_decoding_values_twice(self):
        encoded = urllib.parse.quote(self.query, safe="")
        self.assertEqual(auth._extract_query_string_from_html(
            "<script>queryString='" + encoded + "';</script>"), self.query)

    def test_acip_variant_is_accepted_but_empty_identifiers_are_rejected(self):
        self.assertEqual(auth._validated_query_string(
            "wlanacip=192.0.2.1&wlanuserip=192.0.2.2"),
            "wlanacip=192.0.2.1&wlanuserip=192.0.2.2")
        for query in ("wlanuserip=&wlanacname=Example-AC", "wlanuserip=192.0.2.2&wlanacname="):
            self.assertIsNone(auth._validated_query_string(query))


if __name__ == "__main__":
    unittest.main()
