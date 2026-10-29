"""WebView 桥接层：仅传递公开状态，网络操作在一个后台线程中串行执行。"""

import copy
import logging
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

from core import auth
from ui.branding import APP_NAME

log = logging.getLogger("campus_login.api")
ACCOUNT_LIMIT = 5
LOG_LIMIT = 500


class AppApi:
    def __init__(self, db, settings, on_hide=None, on_quit=None,
                 on_status=None, on_notify=None, on_uninstall=None):
        self._db, self._settings = db, settings
        self._on_hide, self._on_quit = on_hide, on_quit
        self._on_status, self._on_notify = on_status, on_notify
        self._on_uninstall = on_uninstall
        self._uninstall_pending = False
        self._lock = threading.RLock()
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = None
        self._pending = self._busy = self._selected = None
        self._network = {"connected": None, "checking": False, "last_checked": None}
        self._last_event = None
        self._event_id = 0
        self._tray_available = False
        self._failures = 0
        self._logs = deque(maxlen=LOG_LIMIT)
        self._log_id = 0

    def _start(self):
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                return
            self._append_log("info", "应用启动，开始检测网络")
            self._thread = threading.Thread(target=self._run, name="campus-network", daemon=True)
            self._thread.start()

    def _close(self):
        with self._lock:
            if not self._stop.is_set():
                self._append_log("info", "应用退出，已保留守护设置")
            self._stop.set()
            self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _set_tray_available(self, available):
        with self._lock:
            self._tray_available = bool(available)

    def _default_account(self):
        account = self._db.get_account(self._selected) if self._selected else None
        if account is None:
            account = self._db.get_default_account()
            self._selected = account.id if account else None
        return account if account and not account.decrypt_error and account.password else None

    def _daemon_active(self):
        return bool(self._settings.get().daemon_enabled and self._default_account())

    def _event(self, message, level="info"):
        self._event_id += 1
        self._last_event = {"id": self._event_id, "level": level, "message": message}
        self._append_log(level, message)

    def _append_log(self, level, message):
        """记录安全的应用事件；不把含认证报文/密码的原始 HTTP 日志送入页面。"""
        self._log_id += 1
        self._logs.append({"id": self._log_id,
                           "time": datetime.now(timezone.utc).isoformat(),
                           "level": level, "message": message})

    def _state(self):
        accounts = self._db.get_all_accounts()
        if self._selected not in {a.id for a in accounts}:
            default = self._db.get_default_account()
            self._selected = default.id if default else None
        return {
            "accounts": [
                {"id": a.id, "user_id": a.user_id, "service": a.service,
                 "is_default": a.is_default, "decrypt_error": a.decrypt_error}
                for a in accounts
            ],
            "account_limit": ACCOUNT_LIMIT,
            "selected_account_id": self._selected,
            "network": dict(self._network),
            "daemon": {"enabled": self._settings.get().daemon_enabled,
                       "active": self._daemon_active() and not self._stop.is_set(),
                       "account_id": self._selected},
            "busy": self._busy,
            "settings": self._settings.get().to_dict(),
            "services": list(auth.SERVICES),
            "last_event": copy.copy(self._last_event),
            "tray_available": self._tray_available,
            "logs": [dict(entry) for entry in self._logs],
            "log_limit": LOG_LIMIT,
            "can_uninstall": bool(sys.platform == "win32" and getattr(sys, "frozen", False)
                                  and self._on_uninstall is not None),
            "uninstall_pending": self._uninstall_pending,
        }

    def get_state(self):
        with self._lock:
            return self._state()

    def _result(self, ok, message, record=True):
        if record:
            self._append_log("info" if ok else "error", message)
        return {"ok": ok, "message": message, "state": self._state()}

    def clear_logs(self):
        with self._lock:
            self._logs.clear()
            return self._result(True, "运行日志已清空", record=False)

    @staticmethod
    def _account_id(value):
        if type(value) is not int or value < 1:
            raise ValueError("请选择有效的账号")
        return value

    def select_account(self, account_id):
        with self._lock:
            try:
                account_id = self._account_id(account_id)
                account = self._db.get_account(account_id)
                if account is None:
                    raise ValueError("账号不存在，请刷新后重试")
                if account.decrypt_error or not account.password:
                    raise ValueError("密码无法解密，请先编辑账号并重新输入密码")
                if not self._db.set_default(account_id):
                    raise ValueError("无法保存守护账号，请重试")
                self._selected = account_id
                self._failures = 0
                self._wake.set()
                return self._result(True, "已选择守护账号")
            except ValueError as exc:
                return self._result(False, str(exc))
            except Exception:
                log.exception("保存守护账号失败")
                return self._result(False, "无法保存守护账号，请检查数据目录权限")

    def save_account(self, payload):
        with self._lock:
            try:
                if not isinstance(payload, dict):
                    raise ValueError("账号信息格式错误")
                user_id, password = payload.get("user_id", ""), payload.get("password", "")
                service, is_default = payload.get("service", "电信"), payload.get("is_default", False)
                if not isinstance(user_id, str) or not user_id.strip():
                    raise ValueError("请输入学号")
                user_id = user_id.strip()
                if len(user_id) > 128 or not isinstance(password, str) or len(password) > 1024:
                    raise ValueError("学号或密码过长，或格式不正确")
                if service not in auth.SERVICES or type(is_default) is not bool:
                    raise ValueError("请选择有效的运营商和默认账号选项")
                account_id = payload.get("id")
                if account_id is None:
                    accounts = self._db.get_all_accounts()
                    if len(accounts) >= ACCOUNT_LIMIT:
                        raise ValueError("最多支持 5 个账号，请先删除一个账号后再添加")
                    if not password:
                        raise ValueError("请输入密码")
                    is_default = is_default or not accounts
                    saved = self._db.add_account(user_id, password, service, is_default)
                else:
                    account_id = self._account_id(account_id)
                    account = self._db.get_account(account_id)
                    if account is None:
                        raise ValueError("账号不存在")
                    if not password and (account.decrypt_error or not account.password):
                        raise ValueError("原密码无法解密，请重新输入密码")
                    saved = self._db.update_account(
                        account_id, user_id=user_id, password=password or None,
                        service=service, is_default=is_default,
                    )
                if not saved:
                    return self._result(False, "保存失败，学号可能已存在")
                if is_default:
                    self._selected = self._db.get_default_account().id
                self._failures = 0
                self._wake.set()
                return self._result(True, "账号已保存")
            except (ValueError, TypeError) as exc:
                return self._result(False, str(exc))
            except Exception:
                log.exception("保存账号失败")
                return self._result(False, "无法保存账号，请检查数据目录权限")

    def delete_account(self, account_id):
        with self._lock:
            try:
                if not self._db.delete_account(self._account_id(account_id)):
                    raise ValueError("账号不存在")
                self._wake.set()
                return self._result(True, "账号已删除")
            except ValueError as exc:
                return self._result(False, str(exc))
            except Exception:
                log.exception("删除账号失败")
                return self._result(False, "删除失败，请稍后重试")

    def set_default(self, account_id):
        with self._lock:
            try:
                if not self._db.set_default(self._account_id(account_id)):
                    raise ValueError("账号不存在")
                self._selected = account_id
                self._failures = 0
                self._wake.set()
                return self._result(True, "已设为守护账号")
            except ValueError as exc:
                return self._result(False, str(exc))
            except Exception:
                log.exception("设置默认账号失败")
                return self._result(False, "无法设置默认账号")

    def _queue_operation(self, operation):
        if self._stop.is_set():
            return self._result(False, "程序正在退出")
        if self._busy:
            return self._result(False, "正在处理网络操作，请稍候")
        account = None
        if operation == "login":
            self._state()  # 更新已删除的选择，在任务开始前保存账号快照。
            account = self._db.get_account(self._selected) if self._selected else None
            if not account:
                return self._result(False, "请先添加并选择账号")
            if account.decrypt_error or not account.password:
                return self._result(False, "密码无法解密，请编辑账号并重新输入密码")
        else:
            # 明确登出时暂停自动重连，避免守护立即重新登录。
            self._settings.update(daemon_enabled=False)
        self._busy, self._pending = operation, (operation, account)
        self._wake.set()
        return self._result(True, "正在登录…" if operation == "login" else "正在登出…")

    def login(self):
        with self._lock:
            return self._queue_operation("login")

    def logout(self):
        with self._lock:
            try:
                return self._queue_operation("logout")
            except (OSError, ValueError):
                log.exception("无法保存守护设置")
                return self._result(False, "无法暂停守护模式，登出未执行，请检查设置目录权限")

    def toggle_daemon(self, enabled):
        with self._lock:
            try:
                if type(enabled) is not bool:
                    raise ValueError("守护选项格式错误")
                if enabled and not self._default_account():
                    raise ValueError("请先添加可用账号；密码损坏时需重新输入密码")
                self._settings.update(daemon_enabled=enabled)
                self._failures = 0
                self._wake.set()
                return self._result(True, "守护模式已开启" if enabled else "守护模式已暂停")
            except (ValueError, OSError) as exc:
                return self._result(False, str(exc))

    def update_settings(self, payload):
        with self._lock:
            try:
                allowed = {"auto_start", "start_minimized", "check_interval", "show_notifications"}
                if not isinstance(payload, dict) or set(payload) - allowed:
                    raise ValueError("设置格式错误")
                for key, value in payload.items():
                    if key == "check_interval":
                        if type(value) is not int or not 10 <= value <= 300:
                            raise ValueError("检测间隔必须为 10～300 秒的整数")
                    elif type(value) is not bool:
                        raise ValueError("设置选项必须为布尔值")
                self._settings.update(**payload)
                self._wake.set()
                return self._result(True, "设置已保存")
            except (ValueError, OSError) as exc:
                return self._result(False, str(exc))

    def hide_window(self):
        with self._lock:
            if not self._tray_available or not self._on_hide:
                return self._result(False, "系统托盘不可用，窗口将保持显示")
        try:
            self._on_hide()
            with self._lock:
                return self._result(True, "已收起到系统托盘")
        except Exception:
            log.exception("收起窗口失败")
            with self._lock:
                return self._result(False, "无法收起窗口")

    def quit_app(self):
        with self._lock:
            result = self._result(True, "正在退出")
        if self._on_quit:
            timer = threading.Timer(0.15, self._on_quit)
            timer.daemon = True
            timer.start()
        return result

    def uninstall_app(self):
        with self._lock:
            if not (sys.platform == "win32" and getattr(sys, "frozen", False)
                    and self._on_uninstall):
                return self._result(False, "请在 Windows 程序版本中使用卸载")
            if self._busy:
                return self._result(False, "请等待当前网络操作完成后再卸载")
            if self._uninstall_pending:
                return self._result(False, "卸载向导已打开")
            if self._stop.is_set():
                return self._result(False, "程序正在退出")
            self._uninstall_pending = True
            result = self._result(True, "正在打开卸载向导")
        timer = threading.Timer(0.15, self._run_uninstall)
        timer.daemon = True
        timer.start()
        return result

    def _run_uninstall(self):
        try:
            self._on_uninstall()
        except Exception:
            log.exception("卸载向导启动失败")
            with self._lock:
                self._event("无法打开卸载向导，请使用程序旁的卸载脚本", "error")
        finally:
            with self._lock:
                self._uninstall_pending = False

    def _publish_status(self):
        with self._lock:
            if self._stop.is_set():
                return
            connected = self._network["connected"] is True
            active = self._daemon_active() and not self._stop.is_set()
        if self._on_status:
            try:
                self._on_status(connected, active)
            except Exception:
                log.exception("托盘状态更新失败")

    def _network_result(self, connected):
        if self._network["connected"] is not bool(connected):
            self._append_log("success" if connected else "warning",
                             "外网连接正常" if connected else "外网连接已断开")
        self._network.update(connected=bool(connected), checking=False,
                             last_checked=datetime.now(timezone.utc).isoformat())

    def _run_operation(self, operation, account):
        if self._stop.is_set():
            return
        try:
            if operation == "login":
                success = auth.login(account.user_id, account.password, account.service)
            else:
                success = auth.logout()
            connected = auth.check_network() if not self._stop.is_set() else False
            with self._lock:
                self._network_result(connected)
                if operation == "login":
                    success = bool(success and connected)
                    message = "网络已连接" if success else "登录未能连通外网，请检查校园网连接、账号或运营商"
                else:
                    message = "已登出，自动重连已暂停" if success else "登出失败，自动重连已暂停，请检查网络"
                self._event(message, "success" if success else "error")
        except Exception:
            log.exception("网络操作异常")
            message = "网络操作失败，请检查网络后重试"
            with self._lock:
                self._event(message, "error")
        finally:
            with self._lock:
                self._busy = None
                self._network["checking"] = False
            self._publish_status()
        if not self._stop.is_set() and self._on_notify and self._settings.get().show_notifications:
            try:
                self._on_notify(APP_NAME, message)
            except Exception:
                log.exception("系统通知失败")

    def _check_and_reconnect(self):
        with self._lock:
            self._network["checking"] = True
        connected = auth.check_network()
        with self._lock:
            self._network_result(connected)
            if connected:
                self._failures = 0
            account = self._default_account() if self._settings.get().daemon_enabled else None
            reconnect = not connected and account and not self._busy and not self._stop.is_set()
            if reconnect:
                self._busy = "login"
                self._append_log("info", "网络断开，正在使用所选校园网账号重新连接")
        self._publish_status()
        if reconnect:
            self._run_operation("login", account)
            with self._lock:
                self._failures = 0 if self._network["connected"] else self._failures + 1

    def _run(self):
        next_check = 0.0
        while not self._stop.is_set():
            with self._lock:
                changed = self._wake.is_set()
                self._wake.clear()
                pending, self._pending = self._pending, None
            try:
                if pending:
                    self._run_operation(*pending)
                elif changed or time.monotonic() >= next_check:
                    self._check_and_reconnect()
                else:
                    self._wake.wait(max(0, next_check - time.monotonic()))
                    continue
            except Exception:
                log.exception("网络检测异常")
                with self._lock:
                    self._network["checking"] = False
                    self._event("网络检测失败，将在下次检测时重试", "error")
            with self._lock:
                interval = self._settings.get().check_interval
                if self._failures >= auth.CONFIG["max_retries"]:
                    interval = max(interval, auth.CONFIG["retry_interval"] * 10)
                    self._failures = 0
            next_check = time.monotonic() + interval
