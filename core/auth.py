"""
锐捷 ePortal 校园网认证核心模块。
"""

import json
import logging
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import http.client
import http.cookiejar
import threading
from typing import Optional

log = logging.getLogger("campus_login.core")

# Only fixed local messages reach the UI; never retain raw portal responses.
LOGIN_DIAGNOSTICS = {
    "portal_not_found": "未能获取校园网认证参数，请确认校园网连接；浏览器可登录时请检查认证页跳转",
    "portal_unreachable": "无法访问认证页面，请检查校园网连接、路由器和代理设置",
    "portal_http_error": "认证接口返回异常 HTTP 状态，请确认校园网认证服务可用",
    "rsa_unavailable": "认证页面未返回可用的加密公钥，请确认校园网认证页面未发生变化",
    "login_rejected": "认证服务器未接受登录，请检查账号、密码、运营商及账号终端限制",
    "login_bad_response": "认证接口未返回有效的登录结果，请检查认证页面或代理设置",
    "login_transport_error": "认证请求未能完成，请检查校园网连接、路由器和代理设置",
    "login_accepted": "认证服务器已接受登录",
}
_request_state = threading.local()


def clear_login_diagnostic():
    _request_state.diagnostic = None


def get_last_login_diagnostic() -> dict:
    code = getattr(_request_state, "diagnostic", None)
    return {"code": code, "message": LOGIN_DIAGNOSTICS[code]} if code in LOGIN_DIAGNOSTICS else {}


def _login_diagnostic(code):
    _request_state.diagnostic = code
    log.info("%s", LOGIN_DIAGNOSTICS[code])

# ========================= 配置区域 =========================

CONFIG = {
    "portal_server": "http://10.10.9.4",
    "login_url": "http://10.10.9.4/eportal/InterFace.do?method=login",
    "logout_url": "http://10.10.9.4/eportal/InterFace.do?method=logout",
    "online_check_url": "http://10.10.9.4/eportal/InterFace.do?method=getOnlineUserInfo",
    "page_info_url": "http://10.10.9.4/eportal/InterFace.do?method=pageInfo",
    "check_interval": 30,
    "retry_interval": 5,
    "max_retries": 10,
    "connectivity_test_url": "http://connect.rom.miui.com/generate_204",
    "connectivity_test_urls": [
        "http://connect.rom.miui.com/generate_204",
        "http://connectivitycheck.platform.hicloud.com/generate_204",
        "http://www.gstatic.com/generate_204",
        "http://www.msftconnecttest.com/connecttest.txt",
    ],
}

SERVICES = ["电信", "移动", "联通", "校园网"]

# ========================= RSA 加密 =========================


def _rsa_encrypt(message: str, exponent_hex: str, modulus_hex: str) -> str:
    """用 RSA 公钥加密字符串"""
    e = int(exponent_hex, 16)
    m = int(modulus_hex, 16)

    m_hex = format(m, 'x')
    num_digits = (len(m_hex) + 3) // 4
    # Browser RSAKeyPair leaves the highest 16-bit digit empty.
    chunk_size = 2 * (num_digits - 1)
    if e <= 0 or m <= 0 or chunk_size <= 0:
        raise ValueError("Invalid portal RSA public key")

    char_codes = [ord(c) for c in message]
    while len(char_codes) % chunk_size != 0:
        char_codes.append(0)

    result_parts = []

    for i in range(0, len(char_codes), chunk_size):
        block = 0
        for j in range(chunk_size // 2):
            k = i + j * 2
            low = char_codes[k] if k < len(char_codes) else 0
            high = char_codes[k + 1] if (k + 1) < len(char_codes) else 0
            digit_val = low + (high << 8)
            block += digit_val << (16 * j)

        crypt = pow(block, e, m)
        hex_str = format(crypt, 'x')
        hex_str = hex_str.zfill(((len(hex_str) + 3) // 4) * 4)
        result_parts.append(hex_str)

    return " ".join(result_parts)


def encrypt_password(password: str, exponent_hex: str, modulus_hex: str,
                     mac: str = "111111111") -> str:
    """ePortal reverses password + '>' + portal MAC before legacy RSA.

    Behind NAT this must be the upstream MAC from the portal query, rather than
    the computer's local adapter. References/vectors: test_auth_protocol.py.
    """
    reversed_pwd = (password + ">" + (mac or "111111111"))[::-1]
    return _rsa_encrypt(reversed_pwd, exponent_hex, modulus_hex)


# ========================= HTTP 工具 =========================


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_opener(follow_redirects=True):
    # Keep discovery/pageInfo/login cookies in a worker-local browser session.
    if not hasattr(_request_state, "openers"):
        jar = http.cookiejar.CookieJar()
        _request_state.openers = {
            True: urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)),
            False: urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _NoRedirectHandler),
        }
    return _request_state.openers[bool(follow_redirects)]
_default_headers = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}


def _http_get(url: str, *, timeout: int = 10,
              follow_redirects: bool = True) -> tuple[int, str, str]:
    """GET 请求, 返回 (status_code, body, final_url)"""
    req = urllib.request.Request(url, headers=_default_headers)
    try:
        with _http_opener(follow_redirects).open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, resp.url
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body, urllib.parse.urljoin(url, e.headers.get("Location", url))
    except (urllib.error.URLError, http.client.HTTPException, OSError):
        raise


def _http_post(url: str, data: str, *, timeout: int = 10,
               extra_headers: dict | None = None) -> tuple[int, str]:
    """POST 请求, 返回 (status_code, body)"""
    headers = {**_default_headers}
    headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url, data=data.encode("utf-8"), headers=headers, method="POST",
    )
    try:
        with _http_opener().open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body


# ========================= 核心逻辑 =========================


def _connectivity_urls() -> list[str]:
    """返回去重后的外网探测 URL 列表, 兼容旧的单 URL 配置。"""
    urls = []
    configured = CONFIG.get("connectivity_test_urls", [])
    if isinstance(configured, str):
        urls.append(configured)
    else:
        urls.extend(configured)

    legacy_url = CONFIG.get("connectivity_test_url")
    if legacy_url:
        urls.append(legacy_url)

    seen = set()
    result = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _looks_like_portal_response(body: str, final_url: str) -> bool:
    """判断响应是否像校园网认证页。"""
    text = f"{final_url}\n{body[:3000]}".lower()
    markers = (
        "10.10.9.4",
        "/eportal/",
        "interface.do",
        "querystring",
        "wlanuserip",
        "wlanacname",
        "dr.com",
    )
    return any(marker in text for marker in markers)


def _is_online_probe_success(url: str, status: int, body: str,
                             final_url: str) -> bool:
    """判断一次外网探测是否明确成功。"""
    if status == 204:
        return True

    if _looks_like_portal_response(body, final_url):
        return False

    host = urllib.parse.urlparse(url).netloc.lower()
    if "msftconnecttest.com" in host:
        return status == 200 and "Microsoft Connect Test" in body

    return False


def check_network() -> bool:
    """检测是否已经能够正常访问外网"""
    for url in _connectivity_urls():
        try:
            status, body, final_url = _http_get(
                url, timeout=5, follow_redirects=False,
            )
            if _is_online_probe_success(url, status, body, final_url):
                return True
            if _looks_like_portal_response(body, final_url):
                return False
        except (urllib.error.URLError, http.client.HTTPException, OSError):
            continue
    return False


def get_query_string() -> Optional[str]:
    """通过访问外网触发认证重定向, 从重定向 URL 中提取 queryString"""
    received_response = False
    for url in _connectivity_urls():
        try:
            _, body, final_url = _http_get(
                url, timeout=10, follow_redirects=True,
            )
            received_response = True

            query_string = _extract_query_string_from_url(final_url)
            if not query_string:
                query_string = _extract_query_string_from_html(body)

            if query_string:
                log.info("已获取校园网认证参数")
                return query_string
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            log.warning("认证跳转探测未完成（%s）", type(exc).__name__)

    _login_diagnostic("portal_not_found" if received_response else "portal_unreachable")
    return None


def _extract_query_string_from_url(url: str) -> Optional[str]:
    """从认证页 URL 中提取原始 queryString。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    if not parsed.query:
        return None

    return _validated_query_string(parsed.query)


def _validated_query_string(query: str) -> Optional[str]:
    """Preserve raw values and parameter order; unwrap encoded query strings."""
    query = query.strip().lstrip("?")
    for _ in range(3):
        params = {key.lower(): value for key, value in
                  urllib.parse.parse_qs(query, keep_blank_values=True).items()}
        if (any(params.get("wlanuserip", []))
                and (any(params.get("wlanacname", [])) or any(params.get("wlanacip", [])))):
            return query
        unwrapped = urllib.parse.unquote(query)
        if unwrapped == query:
            break
        query = unwrapped
    return None


def _portal_mac(query_string: str) -> str:
    # Browser getQueryStringByName returns the raw, still percent-encoded value.
    match = re.search(r"(?:^|&)mac=([^&]*)", query_string, re.IGNORECASE)
    return match.group(1) if match and match.group(1) else "111111111"


def _js_literal_value(value: str) -> str:
    """Decode URL-related JavaScript escapes without executing page scripts."""
    value = value.replace(r"\/", "/")
    return re.sub(r"\\(?:x([0-9a-fA-F]{2})|u([0-9a-fA-F]{4}))",
                  lambda match: chr(int(match.group(1) or match.group(2), 16)), value)


def _query_from_candidate(value: str) -> Optional[str]:
    value = _js_literal_value(value).strip()
    query = _extract_query_string_from_url(value) if "?" in value else None
    return query or _validated_query_string(value)


def _extract_query_string_from_html(html: str) -> Optional[str]:
    """Read JS/meta redirect parameters without depending on their order."""
    # Decode explicit ampersand entities only. Generic html.unescape would
    # corrupt legitimate query fields such as &copy= or &notin=.
    page = re.sub(r"&(?:amp|#0*38|#x0*26);", "&", html, flags=re.IGNORECASE)
    for match in re.finditer(r"(['\"])(.*?)\1", page, re.DOTALL):
        candidate = match.group(2)
        if "wlanuserip" not in candidate.lower():
            continue
        query = _query_from_candidate(candidate)
        if query:
            return query
    for match in re.finditer(r"(?:https?://|/eportal/)[^\s'\"<>]+", page, re.IGNORECASE):
        query = _query_from_candidate(match.group(0))
        if query:
            return query
    for match in re.finditer(r"wlanuserip=[^\s'\"<>]+", page, re.IGNORECASE):
        query = _validated_query_string(match.group(0))
        if query:
            return query
    return None


def get_rsa_key(query_string: str) -> Optional[tuple[str, str]]:
    """从 pageInfo 接口获取 RSA 公钥 (exponent, modulus)"""
    encoded_qs = urllib.parse.quote(query_string, safe="")
    post_body = f"queryString={encoded_qs}"

    try:
        status, body = _http_post(CONFIG["page_info_url"], data=post_body, timeout=10)
        if not 200 <= status < 300:
            _login_diagnostic("portal_http_error")
            return None
        result = json.loads(body)
        exponent = result.get("publicKeyExponent") if isinstance(result, dict) else None
        modulus = result.get("publicKeyModulus") if isinstance(result, dict) else None

        if (isinstance(exponent, str) and isinstance(modulus, str)
                and int(exponent, 16) > 0 and int(modulus, 16).bit_length() > 16):
            log.info(f"获取到 RSA 公钥")
            return exponent, modulus
        else:
            _login_diagnostic("rsa_unavailable")
            return None
    except (urllib.error.URLError, http.client.HTTPException, OSError):
        _login_diagnostic("portal_unreachable")
        return None
    except (ValueError, TypeError):
        _login_diagnostic("rsa_unavailable")
        return None


def do_login(user_id: str, password: str, service: str) -> bool:
    """执行登录请求"""
    clear_login_diagnostic()
    query_string = get_query_string()
    if not query_string:
        if not get_last_login_diagnostic():
            _login_diagnostic("portal_not_found")
        return False

    rsa_key = get_rsa_key(query_string)
    if not rsa_key:
        if not get_last_login_diagnostic():
            _login_diagnostic("rsa_unavailable")
        return False
    exponent, modulus = rsa_key

    try:
        encrypted_pwd = encrypt_password(password, exponent, modulus, _portal_mac(query_string))
    except (ValueError, TypeError):
        _login_diagnostic("rsa_unavailable")
        return False

    # The page encodes values once, and the form encoder adds another layer.
    # pageInfo above intentionally uses only one layer.
    def login_field(value):
        return urllib.parse.quote(urllib.parse.quote(value, safe=""), safe="")

    post_body = "&".join([
        f"userId={login_field(user_id)}",
        f"password={login_field(encrypted_pwd)}",
        f"service={login_field(service)}",
        f"queryString={login_field(query_string)}",
        "operatorPwd=",
        "operatorUserId=",
        "validcode=",
        "passwordEncrypt=true",
    ])

    extra_headers = {
        "Accept": "*/*",
        "Origin": CONFIG["portal_server"],
        "Referer": f"{CONFIG['portal_server']}/eportal/index.jsp?{query_string}",
    }

    try:
        status, body = _http_post(
            CONFIG["login_url"], data=post_body, timeout=10,
            extra_headers=extra_headers,
        )

        if not 200 <= status < 300:
            _login_diagnostic("portal_http_error")
            return False
        try:
            result = json.loads(body)
            if isinstance(result, dict) and result.get("result") == "success":
                _login_diagnostic("login_accepted")
                return True
            else:
                _login_diagnostic("login_rejected" if isinstance(result, dict) else "login_bad_response")
                return False
        except json.JSONDecodeError:
            _login_diagnostic("login_bad_response")
            return False

    except (urllib.error.URLError, http.client.HTTPException, OSError):
        _login_diagnostic("login_transport_error")
        return False


def get_user_index() -> Optional[str]:
    """获取当前在线用户的 userIndex"""
    try:
        _, body = _http_post(CONFIG["online_check_url"], data="", timeout=10)
        result = json.loads(body)
        return result.get("userIndex")
    except:
        return None


def do_logout() -> bool:
    """执行登出"""
    try:
        user_index = get_user_index() or ""
        post_body = f"userIndex={urllib.parse.quote(user_index, safe='')}"
        _, body = _http_post(CONFIG["logout_url"], data=post_body, timeout=10)
        return "success" in body.lower()
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        log.error("登出请求未完成（%s）", type(exc).__name__)
        return False


def login(user_id: str, password: str, service: str) -> bool:
    """执行一次完整登录流程"""
    clear_login_diagnostic()
    _request_state.__dict__.pop("openers", None)
    log.info("正在尝试校园网认证")
    if check_network():
        log.info("网络已连通, 无需登录")
        return True
    return do_login(user_id, password, service)


def logout() -> bool:
    """登出"""
    return do_logout()


def get_status() -> dict:
    """获取当前状态"""
    connected = check_network()
    return {
        "connected": connected,
        "message": "已连接" if connected else "未连接"
    }


# ========================= 守护模式 =========================


class DaemonManager:
    """守护模式管理器"""

    def __init__(self, db, interval: int = 30):
        self.db = db
        self.interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_status_change = None

    def set_status_callback(self, callback):
        """设置状态变化回调"""
        self._on_status_change = callback

    def _notify_status(self, connected: bool):
        """通知状态变化, 避免 UI 回调异常中断守护流程。"""
        if not self._on_status_change:
            return
        try:
            self._on_status_change(connected)
        except Exception as e:
            log.warning(f"状态回调执行失败: {e}")

    def start(self):
        """启动守护线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("守护模式已启动")

    def stop(self):
        """停止守护线程"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        log.info("守护模式已停止")

    def _run(self):
        """守护循环"""
        consecutive_failures = 0

        while self._running:
            try:
                connected = check_network()

                self._notify_status(connected)

                if connected:
                    if consecutive_failures > 0:
                        log.info("网络恢复正常")
                    consecutive_failures = 0
                else:
                    log.warning("网络断开, 尝试重新登录...")
                    account = self.db.get_default_account()
                    if account:
                        if login(account.user_id, account.password, account.service):
                            consecutive_failures = 0
                            self._notify_status(True)
                        else:
                            consecutive_failures += 1
                    else:
                        consecutive_failures += 1

                    if consecutive_failures >= CONFIG["max_retries"]:
                        log.error(f"连续 {consecutive_failures} 次登录失败")
                        time.sleep(CONFIG["retry_interval"] * 10)
                        consecutive_failures = 0

                time.sleep(self.interval)

            except Exception as e:
                log.error(f"守护模式异常: {e}")
                time.sleep(CONFIG["retry_interval"])


_daemon: Optional[DaemonManager] = None


def daemon_mode(db, interval: int = 30) -> DaemonManager:
    """启动守护模式"""
    global _daemon
    if _daemon is None:
        _daemon = DaemonManager(db, interval)
    _daemon.start()
    return _daemon


def stop_daemon():
    """停止守护模式"""
    global _daemon
    if _daemon:
        _daemon.stop()
        _daemon = None
