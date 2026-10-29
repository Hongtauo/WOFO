"""
配置与设置模块
支持跨平台的开机自启管理
"""

import json
import logging
import os
import platform
import plistlib
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

log = logging.getLogger("campus_login.config")

# 配置目录和文件
CONFIG_DIR = Path.home() / ".config" / "campus_login"
CONFIG_FILE = CONFIG_DIR / "settings.json"


@dataclass
class Settings:
    """设置数据类"""
    auto_start: bool = False
    start_minimized: bool = False
    check_interval: int = 30
    show_notifications: bool = True
    theme: str = "superhero"  # dark theme
    daemon_enabled: bool = True  # 守护模式默认开启

    def to_dict(self) -> dict:
        return {
            "auto_start": self.auto_start,
            "start_minimized": self.start_minimized,
            "check_interval": self.check_interval,
            "show_notifications": self.show_notifications,
            "theme": self.theme,
            "daemon_enabled": self.daemon_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        defaults = cls()
        if not isinstance(data, dict):
            return defaults
        values = defaults.to_dict()
        for key, default in values.items():
            value = data.get(key, default)
            if isinstance(default, bool):
                values[key] = value if isinstance(value, bool) else default
            elif key == "check_interval":
                values[key] = value if type(value) is int and 10 <= value <= 300 else default
            elif key == "theme":
                values[key] = value if isinstance(value, str) and value.strip() else default
        return cls(**values)


class AutoStartManager:
    """开机自启管理器（跨平台）"""

    def __init__(self):
        self.system = platform.system()

    def _get_app_path(self) -> str:
        """获取应用路径"""
        return sys.executable

    def _get_app_args(self) -> list[str]:
        """源码运行时同时指定入口文件，避免自启只打开 Python。"""
        args = [self._get_app_path()]
        if not getattr(sys, "frozen", False):
            args.append(str(Path(__file__).resolve().parent.parent / "main.py"))
        return args

    def _get_desktop_command(self) -> str:
        """按 Desktop Entry 的 Exec 规则转义每个参数。"""
        quoted = []
        for arg in self._get_app_args():
            arg = arg.replace("%", "%%")
            for char in ("\\", '"', "`", "$"):
                arg = arg.replace(char, "\\" + char)
            # Desktop Entry 先应用字符串转义，再应用 Exec 参数转义。
            arg = arg.replace("\\", "\\\\")
            arg = arg.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
            quoted.append('"' + arg + '"')
        return " ".join(quoted)

    def _get_app_name(self) -> str:
        """获取应用名称"""
        return "校园网自动登录"

    def is_enabled(self) -> bool:
        """检查是否已启用开机自启"""
        if self.system == "Windows":
            return self._is_enabled_windows()
        elif self.system == "Darwin":
            return self._is_enabled_macos()
        else:
            return self._is_enabled_linux()

    def enable(self) -> bool:
        """启用开机自启"""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        if self.system == "Windows":
            return self._enable_windows()
        elif self.system == "Darwin":
            return self._enable_macos()
        else:
            return self._enable_linux()

    def disable(self) -> bool:
        """禁用开机自启"""
        if self.system == "Windows":
            return self._disable_windows()
        elif self.system == "Darwin":
            return self._disable_macos()
        else:
            return self._disable_linux()

    # Windows: 使用注册表
    def _is_enabled_windows(self) -> bool:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ
            )
            try:
                value = winreg.QueryValueEx(key, self._get_app_name())
                winreg.CloseKey(key)
                return value[0] is not None
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except Exception:
            return False

    def _enable_windows(self) -> bool:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_WRITE
            )
            command = subprocess.list2cmdline(self._get_app_args())
            winreg.SetValueEx(key, self._get_app_name(), 0, winreg.REG_SZ, command)
            winreg.CloseKey(key)
            log.info("已启用 Windows 开机自启")
            return True
        except Exception as e:
            log.error(f"启用开机自启失败: {e}")
            return False

    def _disable_windows(self) -> bool:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_WRITE
            )
            try:
                winreg.DeleteValue(key, self._get_app_name())
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
            log.info("已禁用 Windows 开机自启")
            return True
        except Exception as e:
            log.error(f"禁用开机自启失败: {e}")
            return False

    # macOS: 使用 LaunchAgents
    def _is_enabled_macos(self) -> bool:
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.campus.login.plist"
        return plist_path.exists()

    def _enable_macos(self) -> bool:
        try:
            plist_content = plistlib.dumps({
                "Label": "com.campus.login",
                "ProgramArguments": self._get_app_args(),
                "RunAtLoad": True,
            })
            plist_path = Path.home() / "Library" / "LaunchAgents"
            plist_path.mkdir(parents=True, exist_ok=True)
            (plist_path / "com.campus.login.plist").write_bytes(plist_content)
            log.info("已启用 macOS 开机自启")
            return True
        except Exception as e:
            log.error(f"启用开机自启失败: {e}")
            return False

    def _disable_macos(self) -> bool:
        try:
            plist_path = Path.home() / "Library" / "LaunchAgents" / "com.campus.login.plist"
            if plist_path.exists():
                plist_path.unlink()
            log.info("已禁用 macOS 开机自启")
            return True
        except Exception as e:
            log.error(f"禁用开机自启失败: {e}")
            return False

    # Linux: 使用 autostart
    def _is_enabled_linux(self) -> bool:
        desktop_path = Path.home() / ".config" / "autostart" / "campus_login.desktop"
        return desktop_path.exists()

    def _enable_linux(self) -> bool:
        try:
            from ui.branding import APP_TITLE, ICON_PNG_PATH
            desktop_content = f"""[Desktop Entry]
Type=Application
Name={APP_TITLE}
Exec={self._get_desktop_command()}
Icon={ICON_PNG_PATH}
Terminal=false
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
            autostart_path = Path.home() / ".config" / "autostart"
            autostart_path.mkdir(parents=True, exist_ok=True)
            (autostart_path / "campus_login.desktop").write_text(desktop_content, encoding="utf-8")
            log.info("已启用 Linux 开机自启")
            return True
        except Exception as e:
            log.error(f"启用开机自启失败: {e}")
            return False

    def _disable_linux(self) -> bool:
        try:
            desktop_path = Path.home() / ".config" / "autostart" / "campus_login.desktop"
            if desktop_path.exists():
                desktop_path.unlink()
            log.info("已禁用 Linux 开机自启")
            return True
        except Exception as e:
            log.error(f"禁用开机自启失败: {e}")
            return False


class SettingsManager:
    """设置管理器"""

    def __init__(self, config_dir: Path = None, auto_start: AutoStartManager = None):
        self.config_dir = Path(config_dir) if config_dir is not None else CONFIG_DIR
        self.config_file = self.config_dir / "settings.json" if config_dir is not None else CONFIG_FILE
        self._lock = threading.RLock()
        self.settings: Settings = Settings()
        self.auto_start = auto_start if auto_start is not None else AutoStartManager()
        self._load()

    def _load(self):
        """加载设置"""
        self.config_dir.mkdir(parents=True, exist_ok=True)

        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                self.settings = Settings.from_dict(data)
            except Exception as e:
                log.error(f"加载设置失败: {e}")
        self.settings.auto_start = self.auto_start.is_enabled()

    @staticmethod
    def _validate(values: dict):
        defaults = Settings().to_dict()
        for key, value in values.items():
            if key not in defaults:
                raise ValueError(f"未知设置: {key}")
            if isinstance(defaults[key], bool) and not isinstance(value, bool):
                raise ValueError(f"{key} 必须为布尔值")
            if key == "check_interval" and not (type(value) is int and 10 <= value <= 300):
                raise ValueError("检测间隔必须为 10 到 300 之间的整数")
            if key == "theme" and (not isinstance(value, str) or not value.strip()):
                raise ValueError("主题必须为非空字符串")

    def _write_settings(self, settings: Settings):
        """在同目录写临时文件后替换，避免保存中断破坏原配置。"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.config_dir,
                prefix=".settings-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(settings.to_dict(), stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.config_file)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self):
        """保存设置"""
        with self._lock:
            self._validate(self.settings.to_dict())
            self._write_settings(self.settings)

    def update(self, **kwargs):
        """更新设置"""
        with self._lock:
            self._validate(kwargs)
            candidate = replace(self.settings, **kwargs)
            previous_auto_start = self.auto_start.is_enabled()
            change_auto_start = (
                "auto_start" in kwargs and candidate.auto_start != previous_auto_start
            )
            if change_auto_start:
                action = self.auto_start.enable if candidate.auto_start else self.auto_start.disable
                if not action():
                    raise OSError("无法更新开机自启设置")
            try:
                self._write_settings(candidate)
            except OSError:
                if change_auto_start:
                    rollback = self.auto_start.enable if previous_auto_start else self.auto_start.disable
                    if not rollback():
                        log.error("配置保存失败后，恢复开机自启状态失败")
                raise
            self.settings = candidate

    def get(self) -> Settings:
        """获取设置"""
        with self._lock:
            return self.settings


# 全局设置实例
_settings: Optional[SettingsManager] = None
_settings_lock = threading.Lock()


def get_settings() -> SettingsManager:
    """获取设置管理器（单例）"""
    global _settings
    with _settings_lock:
        if _settings is None:
            _settings = SettingsManager()
        return _settings
