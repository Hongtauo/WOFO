"""Replay synthetic ePortal responses, including a loopback-only HTTP session."""

import json
import threading
import unittest
import urllib.error
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from core import auth


QUERY = (
    "mac=00%3A11&wlanacname=Example-AC&wlanuserip=192.0.2.44"
    "&url=https%3A%2F%2Fexample.invalid%2F%3Fx%3D1%26y%3D2"
)
REDIRECT = (
    "<script>top.self.location.href='http://portal.invalid/eportal/index.jsp?"
    + QUERY + "';</script>"
)
PAGE_INFO = json.dumps({"publicKeyExponent": "11", "publicKeyModulus": "100040003"})
# Fixed wire fixtures calculated with JavaScript encodeURIComponent, independently
# of the Python implementation. Nested %26 must survive both encoding layers.
EXPECTED_PAGE_BODY = (
    "queryString=mac%3D00%253A11%26wlanacname%3DExample-AC"
    "%26wlanuserip%3D192.0.2.44%26url%3Dhttps%253A%252F%252F"
    "example.invalid%252F%253Fx%253D1%2526y%253D2"
)
EXPECTED_LOGIN_BODY = (
    "userId=fixture%252Buser&password=c339043b%2520487d4d15%25201b9750e4"
    "&service=%25E7%25A7%25BB%25E5%258A%25A8"
    "&queryString=mac%253D00%25253A11%2526wlanacname%253DExample-AC"
    "%2526wlanuserip%253D192.0.2.44%2526url%253Dhttps%25253A%25252F"
    "%25252Fexample.invalid%25252F%25253Fx%25253D1%252526y%25253D2"
    "&operatorPwd=&operatorUserId=&validcode=&passwordEncrypt=true"
)


class LoginReplayTests(unittest.TestCase):
    def setUp(self):
        auth.clear_login_diagnostic()
        self.addCleanup(auth.clear_login_diagnostic)
        get_patch = patch.object(
            auth, "_http_get", return_value=(200, REDIRECT, "http://probe.invalid/check"),
        )
        self.get_mock = get_patch.start()
        self.addCleanup(get_patch.stop)
        probe_patch = patch.object(auth, "_connectivity_urls", return_value=["http://probe.invalid/check"])
        probe_patch.start()
        self.addCleanup(probe_patch.stop)

    def test_js_redirect_mac_binding_and_exact_wire_encoding(self):
        with patch.object(auth, "_http_post", side_effect=[
            (200, PAGE_INFO), (200, '{"result":"success"}'),
        ]) as post, patch.object(
            auth, "encrypt_password", wraps=auth.encrypt_password,
        ) as encrypt, patch("uuid.getnode", side_effect=AssertionError("No local MAC lookup")):
            self.assertTrue(auth.do_login("fixture+user", "abc", "移动"))

        self.assertEqual(post.call_args_list[0].kwargs["data"], EXPECTED_PAGE_BODY)
        self.assertEqual(post.call_args_list[1].kwargs["data"], EXPECTED_LOGIN_BODY)
        encrypt.assert_called_once_with("abc", "11", "100040003", "00%3A11")
        self.assertEqual(auth.get_last_login_diagnostic()["code"], "login_accepted")

    def test_login_response_requires_http_success_and_explicit_json_result(self):
        cases = (
            ((500, '{"result":"success"}'), "portal_http_error"),
            ((403, '<script>function success() {}</script>'), "portal_http_error"),
            ((200, '<script>function success() {}</script>'), "login_bad_response"),
            ((200, '"success"'), "login_bad_response"),
            ((200, '[{"result":"success"}]'), "login_bad_response"),
            ((200, '{"result":"fail","message":"fixture+user raw-server-private"}'), "login_rejected"),
            ((200, '{"result":true}'), "login_rejected"),
            (urllib.error.URLError("raw-server-private"), "login_transport_error"),
        )
        for response, code in cases:
            with self.subTest(code=code, response_type=type(response).__name__), \
                    patch.object(auth, "_http_post", side_effect=[(200, PAGE_INFO), response]), \
                    self.assertLogs(auth.log, level="INFO") as captured:
                self.assertFalse(auth.do_login("fixture+user", "abc", "移动"))
                diagnostic = auth.get_last_login_diagnostic()
                self.assertEqual(diagnostic["code"], code)
            output = "\n".join(captured.output) + json.dumps(diagnostic)
            for private_value in ("fixture+user", QUERY, "192.0.2.44", "00%3A11", "raw-server-private"):
                self.assertNotIn(private_value, output)

    def test_page_info_failure_stops_before_login_and_sets_stage(self):
        cases = (
            ((503, PAGE_INFO), "portal_http_error"),
            ((200, '<html>raw-server-private</html>'), "rsa_unavailable"),
            ((200, '[]'), "rsa_unavailable"),
            ((200, '{"publicKeyExponent":"broken","publicKeyModulus":"ffffffff"}'), "rsa_unavailable"),
            (urllib.error.URLError("raw-server-private"), "portal_unreachable"),
        )
        for response, code in cases:
            with self.subTest(code=code, response_type=type(response).__name__), \
                    patch.object(auth, "_http_post", side_effect=[response]) as post, \
                    self.assertLogs(auth.log, level="INFO") as captured:
                self.assertFalse(auth.do_login("fixture+user", "abc", "移动"))
                self.assertEqual(auth.get_last_login_diagnostic()["code"], code)
                self.assertEqual(post.call_count, 1)
            self.assertNotIn("raw-server-private", "\n".join(captured.output))

    def test_missing_discovery_has_own_stage(self):
        self.get_mock.return_value = (200, "<html>No portal here</html>", "http://probe.invalid/check")
        with patch.object(auth, "_http_post") as post:
            self.assertFalse(auth.do_login("fixture+user", "abc", "移动"))
            self.assertEqual(auth.get_last_login_diagnostic()["code"], "portal_not_found")
            post.assert_not_called()

    def test_failed_discovery_hides_transport_message(self):
        self.get_mock.side_effect = urllib.error.URLError("raw-server-private " + QUERY)
        with patch.object(auth, "_http_post") as post, self.assertLogs(auth.log, level="INFO") as captured:
            self.assertFalse(auth.do_login("fixture+user", "abc", "移动"))
            self.assertEqual(auth.get_last_login_diagnostic()["code"], "portal_unreachable")
            post.assert_not_called()
        self.assertNotIn(QUERY, "\n".join(captured.output))
        self.assertNotIn("raw-server-private", "\n".join(captured.output))


class PortalCookieSessionTests(unittest.TestCase):
    def test_redirect_page_info_and_login_share_cookies(self):
        requests = []

        class Portal(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body="", headers=()):
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                for name, value in headers:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                requests.append((self.path, self.headers.get("Cookie", ""), ""))
                if self.path == "/probe":
                    self.reply(302, headers=(("Location", "/bootstrap"), ("Set-Cookie", "discovery=present; Path=/")))
                else:
                    self.reply(200, REDIRECT, (("Set-Cookie", "session=synthetic; Path=/"),))

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                requests.append((self.path, self.headers.get("Cookie", ""), body))
                if self.path.endswith("method=pageInfo"):
                    self.reply(200, PAGE_INFO, (("Set-Cookie", "phase=pageinfo; Path=/"),))
                elif all(name in cookies for name in ("discovery", "session", "phase")):
                    self.reply(200, '{"result":"success"}')
                else:
                    self.reply(200, '{"result":"fail"}')

        server = ThreadingHTTPServer(("127.0.0.1", 0), Portal)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base = "http://127.0.0.1:" + str(server.server_address[1])
        saved_state = auth._request_state.__dict__.copy()
        auth._request_state.__dict__.clear()
        try:
            with patch("urllib.request.getproxies", return_value={}), patch.dict(auth.CONFIG, {
                "portal_server": base,
                "page_info_url": base + "/eportal/InterFace.do?method=pageInfo",
                "login_url": base + "/eportal/InterFace.do?method=login",
            }), patch.object(auth, "_connectivity_urls", return_value=[base + "/probe"]):
                self.assertTrue(auth.do_login("fixture+user", "abc", "移动"))
        finally:
            auth._request_state.__dict__.clear()
            auth._request_state.__dict__.update(saved_state)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

        self.assertEqual([record[0] for record in requests], [
            "/probe", "/bootstrap", "/eportal/InterFace.do?method=pageInfo",
            "/eportal/InterFace.do?method=login",
        ])
        self.assertEqual(requests[2][2], EXPECTED_PAGE_BODY)
        self.assertEqual(requests[3][2], EXPECTED_LOGIN_BODY)
        self.assertIn("discovery=present", requests[1][1])
        self.assertIn("session=synthetic", requests[2][1])
        self.assertIn("phase=pageinfo", requests[3][1])


if __name__ == "__main__":
    unittest.main()
