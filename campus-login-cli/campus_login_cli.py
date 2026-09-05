#!/usr/bin/env python3
"""Dr.COM ePortal 校园网登录器（macOS/Linux 纯命令行版）。"""

import argparse
import getpass
import html
import http.client
import json
import logging
import os
import re
import signal
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional, Sequence, Tuple


LOG = logging.getLogger("WOFOLogin")
SERVICES = ("电信", "移动", "联通", "校园网")
PROBE_URLS = (
    "http://connect.rom.miui.com/generate_204",
    "http://connectivitycheck.platform.hicloud.com/generate_204",
    "http://www.gstatic.com/generate_204",
    "http://www.msftconnecttest.com/connecttest.txt",
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
}


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler)


class PortalClient:
    """Dr.COM ePortal 协议客户端，仅依赖 Python 标准库。"""

    def __init__(self, portal: str, timeout: int = 10):
        portal = portal.strip().rstrip("/")
        parsed = urllib.parse.urlparse(portal)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("--portal 必须是完整的 http/https URL")
        self.portal = portal
        self.interface = portal + "/eportal/InterFace.do"
        self.timeout = timeout

    def _get(self, url: str, follow_redirects: bool = True) -> Tuple[int, str, str]:
        request = urllib.request.Request(url, headers=HEADERS)
        try:
            if follow_redirects:
                response = urllib.request.urlopen(request, timeout=self.timeout)
            else:
                response = NO_REDIRECT_OPENER.open(request, timeout=self.timeout)
            with response:
                body = response.read().decode("utf-8", errors="replace")
                return response.status, body, response.url
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return exc.code, body, exc.headers.get("Location", url)

    def _post(
        self, url: str, data: str, extra_headers: Optional[Dict[str, str]] = None
    ) -> Tuple[int, str]:
        headers = dict(HEADERS)
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(
            url, data=data.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return exc.code, body

    @staticmethod
    def _looks_like_portal(body: str, final_url: str) -> bool:
        text = (final_url + "\n" + body[:3000]).lower()
        return any(
            marker in text
            for marker in (
                "/eportal/", "interface.do", "querystring", "wlanuserip",
                "wlanacname", "dr.com",
            )
        )

    def is_online(self) -> bool:
        for url in PROBE_URLS:
            try:
                status, body, final_url = self._get(url, follow_redirects=False)
            except (urllib.error.URLError, http.client.HTTPException, OSError):
                continue
            if status == 204:
                return True
            if self._looks_like_portal(body, final_url):
                return False
            if (
                "msftconnecttest.com" in urllib.parse.urlparse(url).netloc.lower()
                and status == 200
                and "Microsoft Connect Test" in body
            ):
                return True
        return False

    @staticmethod
    def _query_from_url(url: str) -> Optional[str]:
        query = urllib.parse.urlparse(url).query
        if not query:
            return None
        fields = urllib.parse.parse_qs(query)
        if "wlanuserip" in fields and "wlanacname" in fields:
            return query
        return None

    @staticmethod
    def _query_from_html(page: str) -> Optional[str]:
        patterns = (
            r"queryString\s*=\s*['\"]([^'\"]+)['\"]",
            r"wlanuserip=[\w.]+&wlanacname=[\w-]+[^'\"<>\s]*",
        )
        for pattern in patterns:
            match = re.search(pattern, page)
            if match:
                return html.unescape(match.group(1) if match.lastindex else match.group(0))
        return None

    def get_query_string(self) -> Optional[str]:
        for url in PROBE_URLS:
            try:
                _, body, final_url = self._get(url, follow_redirects=True)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                LOG.debug("通过 %s 获取认证参数失败: %s", url, exc)
                continue
            query = self._query_from_url(final_url) or self._query_from_html(body)
            if query:
                return query
        return None

    @staticmethod
    def encrypt_password(password: str, exponent_hex: str, modulus_hex: str) -> str:
        """复制 ePortal JavaScript 的反转密码 + RSA 分块算法。"""
        exponent = int(exponent_hex, 16)
        modulus = int(modulus_hex, 16)
        digits = (len(format(modulus, "x")) + 3) // 4
        chunk_size = 2 * digits
        codes = [ord(char) for char in password[::-1]]
        codes.extend([0] * ((-len(codes)) % chunk_size))
        encrypted = []
        for offset in range(0, len(codes), chunk_size):
            block = 0
            for index in range(chunk_size // 2):
                low = codes[offset + index * 2]
                high = codes[offset + index * 2 + 1]
                block += (low + (high << 8)) << (16 * index)
            encrypted.append(format(pow(block, exponent, modulus), "x"))
        return " ".join(encrypted)

    def _rsa_key(self, query: str) -> Optional[Tuple[str, str]]:
        _, body = self._post(
            self.interface + "?method=pageInfo",
            urllib.parse.urlencode({"queryString": query}),
        )
        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            return None
        exponent = result.get("publicKeyExponent")
        modulus = result.get("publicKeyModulus")
        return (exponent, modulus) if exponent and modulus else None

    def login(self, user: str, password: str, service: str) -> bool:
        if self.is_online():
            LOG.info("网络已连通，无需重复登录")
            return True
        query = self.get_query_string()
        if not query:
            LOG.error("未获取到认证参数，请确认已连接校园网")
            return False
        key = self._rsa_key(query)
        if not key:
            LOG.error("认证门户未返回 RSA 公钥")
            return False
        password_cipher = self.encrypt_password(password, key[0], key[1])
        data = urllib.parse.urlencode({
            "userId": user,
            "password": password_cipher,
            "service": service,
            "queryString": query,
            "operatorPwd": "",
            "operatorUserId": "",
            "validcode": "",
            "passwordEncrypt": "true",
        })
        _, body = self._post(
            self.interface + "?method=login",
            data,
            {
                "Accept": "*/*",
                "Origin": self.portal,
                "Referer": self.portal + "/eportal/index.jsp?" + query,
            },
        )
        try:
            result = json.loads(body)
            if result.get("result") == "success":
                return True
            LOG.error("登录失败: %s", result.get("message", "未知错误"))
            return False
        except json.JSONDecodeError:
            return "success" in body.lower()

    def logout(self) -> bool:
        _, info_body = self._post(
            self.interface + "?method=getOnlineUserInfo", ""
        )
        try:
            user_index = json.loads(info_body).get("userIndex", "")
        except json.JSONDecodeError:
            user_index = ""
        _, body = self._post(
            self.interface + "?method=logout",
            urllib.parse.urlencode({"userIndex": user_index}),
        )
        try:
            return json.loads(body).get("result") == "success"
        except json.JSONDecodeError:
            return "success" in body.lower()


def _password(args) -> str:
    password = os.environ.get(args.password_env)
    if password:
        return password
    if not sys.stdin.isatty():
        raise RuntimeError(
            "非交互运行时请设置环境变量 " + args.password_env
        )
    return getpass.getpass("校园网密码: ")


def _user(args) -> str:
    user = args.user or os.environ.get("CAMPUS_LOGIN_USER")
    if not user:
        raise RuntimeError("请使用 --user 或 CAMPUS_LOGIN_USER 提供校园网账号")
    return user


def _run_daemon(client: PortalClient, args) -> int:
    user = _user(args)
    password = _password(args)
    stop = threading.Event()

    def request_stop(_signum=None, _frame=None):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    LOG.info("守护模式已启动，检测间隔 %s 秒", args.interval)
    while not stop.is_set():
        try:
            if client.is_online():
                LOG.info("网络正常")
            else:
                LOG.warning("网络断开，尝试重新登录")
                if client.login(user, password, args.service):
                    LOG.info("重新登录成功")
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            LOG.warning("网络请求失败: %s", exc)
        stop.wait(args.interval)
    LOG.info("守护模式已停止")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wofologin", description="WOFOLogin Dr.COM ePortal 校园网命令行登录器"
    )
    parser.add_argument(
        "--portal", default=os.environ.get("CAMPUS_LOGIN_PORTAL", "http://10.10.9.4"),
        help="ePortal 根地址（默认: %(default)s）",
    )
    parser.add_argument("--timeout", type=int, default=10, help="HTTP 超时秒数")
    parser.add_argument("--verbose", action="store_true", help="显示调试日志")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="检查是否已连通外网")
    commands.add_parser("logout", help="登出校园网")

    for name, help_text in (("login", "登录校园网"), ("daemon", "断网自动重登")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--user", help="校园网账号（或设置 CAMPUS_LOGIN_USER）"
        )
        command.add_argument(
            "--service", choices=SERVICES,
            default=os.environ.get("CAMPUS_LOGIN_SERVICE", "电信"), help="运营商",
        )
        command.add_argument(
            "--password-env", default="CAMPUS_LOGIN_PASSWORD",
            help="读取密码的环境变量名",
        )
        if name == "daemon":
            command.add_argument("--interval", type=int, default=30, help="检测间隔秒数")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout < 1 or getattr(args, "interval", 1) < 1:
        parser.error("超时和间隔必须大于 0")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        client = PortalClient(args.portal, args.timeout)
        if args.command == "status":
            online = client.is_online()
            print("已连接" if online else "未连接")
            return 0 if online else 1
        if args.command == "logout":
            success = client.logout()
            print("已登出" if success else "登出失败")
            return 0 if success else 1
        if args.command == "daemon":
            return _run_daemon(client, args)
        success = client.login(_user(args), _password(args), args.service)
        print("登录成功" if success else "登录失败")
        return 0 if success else 1
    except (RuntimeError, ValueError) as exc:
        parser.exit(2, "错误: {}\n".format(exc))
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        LOG.error("网络请求失败: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
