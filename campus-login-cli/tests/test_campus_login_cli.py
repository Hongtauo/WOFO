import json
import os
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campus_login_cli as cli


class PortalClientTests(unittest.TestCase):
    def setUp(self):
        self.client = cli.PortalClient("http://10.10.9.4")

    def test_rejects_invalid_portal(self):
        with self.assertRaises(ValueError):
            cli.PortalClient("10.10.9.4")

    def test_extracts_redirect_query_verbatim(self):
        query = "wlanuserip=10.1.2.3&wlanacname=AC-1&ssid=a%20b"
        self.assertEqual(
            self.client._query_from_url("http://10.10.9.4/eportal/?" + query), query
        )

    def test_extracts_html_query(self):
        page = (
            '<script>queryString = "wlanuserip=10.1.2.3&amp;'
            'wlanacname=AC-1";</script>'
        )
        self.assertEqual(
            self.client._query_from_html(page),
            "wlanuserip=10.1.2.3&wlanacname=AC-1",
        )

    def test_rsa_algorithm_is_deterministic(self):
        self.assertEqual(
            self.client.encrypt_password("abc", "11", "ca1"),
            self.client.encrypt_password("abc", "11", "ca1"),
        )

    @mock.patch.object(cli.PortalClient, "is_online", return_value=False)
    @mock.patch.object(
        cli.PortalClient,
        "get_query_string",
        return_value="wlanuserip=10.1.2.3&wlanacname=AC-1",
    )
    @mock.patch.object(cli.PortalClient, "_rsa_key", return_value=("11", "ca1"))
    @mock.patch.object(cli.PortalClient, "encrypt_password", return_value="cipher text")
    @mock.patch.object(cli.PortalClient, "_post")
    def test_login_encodes_protocol_fields(self, post, _encrypt, _key, _query, _online):
        post.return_value = (200, json.dumps({"result": "success"}))
        self.assertTrue(self.client.login("student+1", "secret", "校园网"))
        fields = urllib.parse.parse_qs(post.call_args.args[1], keep_blank_values=True)
        self.assertEqual(fields["userId"], ["student+1"])
        self.assertEqual(fields["password"], ["cipher text"])
        self.assertEqual(fields["service"], ["校园网"])
        self.assertEqual(fields["passwordEncrypt"], ["true"])


class CredentialTests(unittest.TestCase):
    def test_user_can_come_from_environment(self):
        args = mock.Mock(user=None)
        with mock.patch.dict(os.environ, {"CAMPUS_LOGIN_USER": "student"}):
            self.assertEqual(cli._user(args), "student")

    def test_missing_noninteractive_password_is_rejected(self):
        args = mock.Mock(password_env="CAMPUS_LOGIN_TEST_PASSWORD")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            sys.stdin, "isatty", return_value=False
        ):
            with self.assertRaises(RuntimeError):
                cli._password(args)


if __name__ == "__main__":
    unittest.main()
