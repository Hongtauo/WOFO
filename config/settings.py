"""
配置与设置模块
支持跨平台的开机自启管理
"""

import json
import logging
import platform
import sys
from dataclasses import dataclass, field
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
        return cls(
            auto_start=data.get("auto_start", False),
            start_minimized=data.get("start_minimized", False),
            check_interval=data.get("check_interval", 30),
            show_notifications=data.get("show_notifications", True),
            theme=data.get("theme", "superhero"),
            daemon_enabled=data.get("daemon_enabled", True),
        )


class AutoStartManager:
    """开机自启管理器（跨平台）"""

    def __init__(self):
        self.system = platform.system()

    def _get_app_path(self) -> str:
        """获取应用路径"""
        if getattr(sys, 'frozen', False):
            return sys.executable
        return sys.executable

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
            winreg.SetValueEx(key, self._get_app_name(), 0, winreg.REG_SZ, self._get_app_path())
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
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.campus.login</string>
    <key>ProgramArguments</key>
    <array>
        <string>{self._get_app_path()}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>"""
            plist_path = Path.home() / "Library" / "LaunchAgents"
            plist_path.mkdir(parents=True, exist_ok=True)
            (plist_path / "com.campus.login.plist").write_text(plist_content)
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
            desktop_content = f"""[Desktop Entry]
Type=Application
Name={self._get_app_name()}
Exec={self._get_app_path()}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
            autostart_path = Path.home() / ".config" / "autostart"
            autostart_path.mkdir(parents=True, exist_ok=True)
            (autostart_path / "campus_login.desktop").write_text(desktop_content)
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

    def __init__(self):
        self.settings: Settings = Settings()
        self.auto_start = AutoStartManager()
        self._load()

    def _load(self):
        """加载设置"""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                self.settings = Settings.from_dict(data)
                # 同步自启状态
                self.settings.auto_start = self.auto_start.is_enabled()
            except Exception as e:
                log.error(f"加载设置失败: {e}")

    def save(self):
        """保存设置"""
        try:
            CONFIG_FILE.write_text(
                json.dumps(self.settings.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            log.error(f"保存设置失败: {e}")

    def update(self, **kwargs):
        """更新设置"""
        for key, value in kwargs.items():
            if hasattr(self.settings, key):
                old_value = getattr(self.settings, key)
                setattr(self.settings, key, value)

                # 同步自启设置
                if key == "auto_start":
                    if value:
                        self.auto_start.enable()
                    else:
                        self.auto_start.disable()

        self.save()

    def get(self) -> Settings:
        """获取设置"""
        return self.settings


# 全局设置实例
_settings: Optional[SettingsManager] = None


def get_settings() -> SettingsManager:
    """获取设置管理器（单例）"""
    global _settings
    if _settings is None:
        _settings = SettingsManager()
    return _settings
