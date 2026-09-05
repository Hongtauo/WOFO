"""
校园网认证核心模块
从 campus_login.py 迁移的核心逻辑
"""

import json
import logging
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import http.client
import threading
import html as html_utils
from typing import Optional

log = logging.getLogger("campus_login.core")

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
    chunk_size = 2 * num_digits

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
        result_parts.append(hex_str)

    return " ".join(result_parts)


def encrypt_password(password: str, exponent_hex: str, modulus_hex: str) -> str:
    """加密密码（反转 + RSA）"""
    reversed_pwd = password[::-1]
    return _rsa_encrypt(reversed_pwd, exponent_hex, modulus_hex)


# ========================= HTTP 工具 =========================


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_no_redirect_opener = urllib.request.build_opener(_NoRedirectHandler)
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
        if follow_redirects:
            resp = urllib.request.urlopen(req, timeout=timeout)
        else:
            resp = _no_redirect_opener.open(req, timeout=timeout)
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body, resp.url
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body, e.headers.get("Location", url)
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
        resp = urllib.request.urlopen(req, timeout=timeout)
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
    for url in _connectivity_urls():
        try:
            _, body, final_url = _http_get(
                url, timeout=10, follow_redirects=True,
            )
            log.info(f"重定向最终 URL: {final_url}")

            query_string = _extract_query_string_from_url(final_url)
            if not query_string:
                query_string = _extract_query_string_from_html(body)

            if query_string:
                log.info(f"提取到 queryString: {query_string[:80]}...")
                return query_string
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            log.warning(f"通过 {url} 获取 queryString 失败: {e}")

    log.error("获取 queryString 失败: 所有探测 URL 均未返回认证参数")
    return None


def _extract_query_string_from_url(url: str) -> Optional[str]:
    """从认证页 URL 中提取原始 queryString。"""
    parsed = urllib.parse.urlparse(url)
    if not parsed.query:
        return None

    params = urllib.parse.parse_qs(parsed.query)
    if "wlanuserip" not in params or "wlanacname" not in params:
        return None

    return parsed.query


def _extract_query_string_from_html(html: str) -> Optional[str]:
    """从 HTML 页面中提取 queryString"""
    patterns = [
        r"queryString\s*=\s*['\"]([^'\"]+)['\"]",
        r"wlanuserip=[\w.]+&wlanacname=[\w-]+[^'\"<>\s]*",
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            qs = match.group(1) if match.lastindex else match.group(0)
            return html_utils.unescape(qs)
    return None


def get_rsa_key(query_string: str) -> Optional[tuple[str, str]]:
    """从 pageInfo 接口获取 RSA 公钥 (exponent, modulus)"""
    encoded_qs = urllib.parse.quote(query_string, safe="")
    post_body = f"queryString={encoded_qs}"

    try:
        _, body = _http_post(CONFIG["page_info_url"], data=post_body, timeout=10)
        result = json.loads(body)
        exponent = result.get("publicKeyExponent")
        modulus = result.get("publicKeyModulus")

        if exponent and modulus:
            log.info(f"获取到 RSA 公钥")
            return exponent, modulus
        else:
            log.warning(f"pageInfo 响应中缺少公钥")
            return None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        log.error(f"获取 RSA 公钥失败: {e}")
        return None
    except json.JSONDecodeError:
        log.error(f"pageInfo 响应不是有效 JSON")
        return None


def do_login(user_id: str, password: str, service: str) -> bool:
    """执行登录请求"""
    query_string = get_query_string()
    if not query_string:
        log.error("无法获取 queryString, 可能未连接到校园网 WiFi")
        return False

    rsa_key = get_rsa_key(query_string)
    if not rsa_key:
        log.error("无法获取 RSA 公钥")
        return False
    exponent, modulus = rsa_key

    encrypted_pwd = encrypt_password(password, exponent, modulus)
    encoded_query_string = urllib.parse.quote(query_string, safe="")

    post_body = "&".join([
        f"userId={urllib.parse.quote(user_id, safe='')}",
        f"password={urllib.parse.quote(encrypted_pwd, safe='')}",
        f"service={urllib.parse.quote(service, safe='')}",
        f"queryString={encoded_query_string}",
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

        log.info(f"登录响应: {body[:200]}")

        try:
            result = json.loads(body)
            if result.get("result") == "success":
                log.info("登录成功")
                return True
            else:
                msg = result.get("message", "未知错误")
                log.warning(f"登录失败: {msg}")
                return False
        except json.JSONDecodeError:
            return "success" in body.lower()

    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        log.error(f"登录请求异常: {e}")
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
        log.info(f"登出响应: {body[:200]}")
        return "success" in body.lower()
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        log.error(f"登出失败: {e}")
        return False


def login(user_id: str, password: str, service: str) -> bool:
    """执行一次完整登录流程"""
    log.info(f"正在登录 (用户: {user_id}, 服务: {service})...")
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
