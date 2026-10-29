"""Per-user Windows installation and removal for the frozen, single-file app.

The public entry points only return status codes: 0 succeeds, 1 fails, and 2
means the user canceled. They never terminate the application themselves.
"""

from __future__ import annotations

import base64
import ctypes
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ui.branding import APP_DISPLAY_NAME, APP_NAME, APP_TITLE, APP_VERSION

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\WOFO Login"
AUTORUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DATA_FILENAMES = ("accounts.db", "key.key", "settings.json")
SUCCESS, ERROR, CANCELED = 0, 1, 2


@dataclass(frozen=True)
class AppPaths:
    install_dir: Path
    executable: Path
    desktop_shortcut: Path
    menu_dir: Path
    menu_shortcut: Path
    uninstall_shortcut: Path
    data_dir: Path


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _known_folder(csidl: int, fallback: Path) -> Path:
    """Honor redirected Desktop/Start Menu folders, including OneDrive."""
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0:
        return Path(buffer.value).resolve()
    return fallback.resolve()


def _paths() -> AppPaths:
    local = os.environ.get("LOCALAPPDATA")
    roaming = os.environ.get("APPDATA")
    if not local or not roaming or not Path(local).is_absolute() or not Path(roaming).is_absolute():
        raise RuntimeError("无法确定当前用户的应用程序目录。")
    # The fixed leaf is deliberately checked before any file operations.
    base = Path(local).resolve() / "Programs"
    install = base / APP_NAME
    if not _same_path(install.resolve().parent, base.resolve()) or not _same_path(install.resolve(), install.absolute()):
        raise RuntimeError("安装目录指向了预期位置以外的路径。")
    executable = install / f"{APP_NAME}.exe"
    if not _same_path(executable.resolve(), executable.absolute()):
        raise RuntimeError("安装文件是链接，无法安全覆盖。")
    desktop = _known_folder(0x10, Path.home() / "Desktop")
    programs = _known_folder(0x02, Path(roaming) / "Microsoft/Windows/Start Menu/Programs")
    menu = programs / APP_NAME
    if not _same_path(menu.resolve().parent, programs.resolve()) or not _same_path(menu.resolve(), menu.absolute()):
        raise RuntimeError("开始菜单目录指向了预期位置以外的路径。")
    data = Path.home() / ".config" / "campus_login"
    if not _same_path(data.resolve(), data.absolute()):
        raise RuntimeError("账号数据目录是链接，无法安全处理。")
    return AppPaths(
        install, executable, desktop / f"{APP_NAME}.lnk",
        menu, menu / f"{APP_NAME}.lnk", menu / f"卸载 {APP_NAME}.lnk", data,
    )


def _message(text: str, *, confirm: bool = False, error: bool = False) -> bool:
    # Both confirmation dialogs default to No. The data question is separate
    # so keeping accounts requires no extra opt-in.
    flags = (0x04 | 0x100 | 0x20) if confirm else (0x10 if error else 0x40)
    if sys.platform != "win32":
        print(f"{APP_NAME}: {text}", file=sys.stderr if error else sys.stdout)
        return False
    return ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, flags) == 6


def _frozen_executable() -> Path:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise RuntimeError("安装和卸载仅适用于打包后的 Windows EXE。\n请使用发布目录中的 WOFO Login.exe。")
    executable = Path(sys.executable).resolve(strict=True)
    if executable.suffix.lower() != ".exe" or not executable.is_file():
        raise RuntimeError("无法确认当前程序的 EXE 路径。")
    return executable


def _quote_ps(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell() -> str:
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return str(root / "System32/WindowsPowerShell/v1.0/powershell.exe")


def _encoded_command(script: str) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [_powershell(), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded]


def _run_powershell(script: str) -> None:
    subprocess.run(
        _encoded_command(script), check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30,
    )


def _read_registration() -> dict[str, str]:
    import winreg

    values = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, value, _kind = winreg.EnumValue(key, index)
                values[name] = value
            if not values:
                values["__empty_key__"] = ""
    except FileNotFoundError:
        pass
    return values


def _uninstall_command(executable: Path) -> str:
    # Always quote the EXE, even if its path happens not to contain spaces.
    return f'"{executable}" --uninstall'


def _registration_owned(values: dict[str, str], executable: Path) -> bool:
    return (
        values.get("DisplayName") in {APP_NAME, APP_DISPLAY_NAME}
        and _same_path(values.get("InstallLocation", ""), executable.parent)
        and values.get("UninstallString", "").casefold() == _uninstall_command(executable).casefold()
    )


def _register_install(paths: AppPaths) -> None:
    import winreg

    strings = {
        "DisplayName": APP_DISPLAY_NAME, "InstallLocation": str(paths.install_dir),
        "UninstallString": _uninstall_command(paths.executable),
        "DisplayVersion": APP_VERSION, "Publisher": "WOFO",
        "DisplayIcon": f'"{paths.executable}",0',
    }
    desired = {name: (value, winreg.REG_SZ) for name, value in strings.items()}
    desired.update({name: (1, winreg.REG_DWORD) for name in ("NoModify", "NoRepair")})
    previous = {}
    existed = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            existed = True
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, value, kind = winreg.EnumValue(key, index)
                previous[name] = (value, kind)
    except FileNotFoundError:
        pass
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
            for name, (value, kind) in desired.items():
                winreg.SetValueEx(key, name, 0, kind, value)
    except Exception:
        # Revert only fields still equal to the values written by this attempt.
        # Concurrent, unrelated changes and registry subkeys are preserved.
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                for name, expected in desired.items():
                    try:
                        current = winreg.QueryValueEx(key, name)
                    except FileNotFoundError:
                        continue
                    if current == expected:
                        if name in previous:
                            old_value, old_kind = previous[name]
                            winreg.SetValueEx(key, name, 0, old_kind, old_value)
                        else:
                            winreg.DeleteValue(key, name)
                empty = winreg.QueryInfoKey(key)[:2] == (0, 0)
            if not existed and empty:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except OSError:
            pass
        raise


def _retarget_autorun(command: str, source: Path, target: Path) -> str | None:
    quoted_source = f'"{source}"'
    quoted_target = f'"{target}"'
    if command.casefold() in (str(source).casefold(), quoted_source.casefold()):
        return quoted_target
    if command.casefold().startswith((quoted_source + " ").casefold()):
        return quoted_target + command[len(quoted_source):]
    return None


def _owned_executables(executable: Path) -> tuple[Path, ...]:
    # Previous releases used these exact names. They are considered only for
    # Run-value migration/removal; their files are never removed by this app.
    return (executable, executable.parent / "校园网自动登录.exe", executable.parent / "校园网守护.exe")


def _migrate_autorun(source: Path, target: Path) -> None:
    """Retarget only this portable EXE's existing Run values; keep its args."""
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTORUN_KEY, 0, winreg.KEY_READ | winreg.KEY_WRITE)
    except FileNotFoundError:
        return
    changed = []
    with key:
        try:
            for name in ("校园网自动登录", APP_NAME):
                try:
                    original, kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                replacement = None
                if isinstance(original, str):
                    for candidate in _owned_executables(source):
                        replacement = _retarget_autorun(original, candidate, target)
                        if replacement is not None:
                            break
                if replacement is not None and replacement != original:
                    winreg.SetValueEx(key, name, 0, kind, replacement)
                    changed.append((name, original, kind, replacement))
        except Exception:
            for name, original, kind, replacement in changed:
                if winreg.QueryValueEx(key, name)[0] == replacement:
                    winreg.SetValueEx(key, name, 0, kind, original)
            raise


def _assert_plain_file_target(path: Path) -> None:
    if not _same_path(path.resolve(), path.absolute()):
        raise RuntimeError("安装文件是链接，无法安全覆盖。")
    if path.exists():
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise RuntimeError("安装文件是重解析点，无法安全覆盖。")


def _atomic_copy(source: Path, destination: Path) -> None:
    _assert_plain_file_target(destination)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".wofo-install-", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        _assert_plain_file_target(destination)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _cleanup_first_install(paths: AppPaths, new_shortcuts: list[Path], signature: os.stat_result | None) -> None:
    if new_shortcuts:
        link_entries = ",".join(_quote_ps(path) for path in new_shortcuts)
        _run_powershell(f"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($path in @({link_entries})) {{
    if (Test-Path -LiteralPath $path) {{
        $link = $shell.CreateShortcut($path)
        if ($link.TargetPath -ieq {_quote_ps(paths.executable)} -and $link.Arguments -in @('', '--uninstall')) {{
            Remove-Item -LiteralPath $path -ErrorAction Stop
        }}
    }}
}}
""")
    if signature is not None and paths.executable.exists():
        _assert_plain_file_target(paths.executable)
        current = paths.executable.stat()
        if (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) == (signature.st_dev, signature.st_ino, signature.st_mtime_ns, signature.st_size):
            paths.executable.unlink()
    for directory in (paths.menu_dir, paths.install_dir):
        try:
            directory.rmdir()  # Empty directories only; never recurse.
        except OSError:
            pass


def _create_shortcuts(paths: AppPaths) -> None:
    exe = _quote_ps(paths.executable)
    shortcuts = [(paths.desktop_shortcut, ""), (paths.menu_shortcut, ""), (paths.uninstall_shortcut, "--uninstall")]
    entries = ",\n".join(
        f"@{{ Path = {_quote_ps(path)}; Arguments = {_quote_ps(args)} }}" for path, args in shortcuts
    )
    _run_powershell(f"""
$ErrorActionPreference = 'Stop'
$target = {exe}
$links = @({entries})
$shell = New-Object -ComObject WScript.Shell
# Check all destinations before replacing any shortcut.
foreach ($entry in $links) {{
    if (Test-Path -LiteralPath $entry.Path) {{
        $existing = $shell.CreateShortcut($entry.Path)
        if ($existing.TargetPath -ine $target -or $existing.Arguments -ne $entry.Arguments) {{
            throw '同名快捷方式属于其他程序，未覆盖。'
        }}
    }}
}}
foreach ($entry in $links) {{
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($entry.Path)) | Out-Null
    $link = $shell.CreateShortcut($entry.Path)
    $link.TargetPath = $target
    $link.Arguments = $entry.Arguments
    $link.WorkingDirectory = {_quote_ps(paths.install_dir)}
    $link.IconLocation = $target + ',0'
    $link.Description = {_quote_ps(APP_TITLE)}
    $link.Save()
}}
""")


def run_install() -> int:
    """Copy the single EXE to the current user's Programs folder."""
    paths = None
    new_install = False
    new_shortcuts = []
    signature = None
    try:
        source = _frozen_executable()
        paths = _paths()
        _assert_plain_file_target(paths.executable)
        registration = _read_registration()
        if registration and not _registration_owned(registration, paths.executable):
            raise RuntimeError("同名安装记录不属于此安装位置，未覆盖。")
        if paths.executable.exists() and not _same_path(source, paths.executable) and not _registration_owned(registration, paths.executable):
            raise RuntimeError("安装目录存在同名文件且无法确认归属，未覆盖。")
        if not _message(
            f"将 {APP_NAME} 安装到当前用户目录：\n{paths.install_dir}\n\n"
            "会创建桌面和开始菜单快捷方式，并添加到 Windows“已安装的应用”。\n"
            "账号和设置将继续保留，无需管理员权限。\n\n是否继续？", confirm=True,
        ):
            return CANCELED
        new_install = not paths.executable.exists()
        new_shortcuts = [path for path in (paths.desktop_shortcut, paths.menu_shortcut, paths.uninstall_shortcut) if not path.exists()]
        paths.install_dir.mkdir(parents=True, exist_ok=True)
        if not _same_path(source, paths.executable):
            try:
                _atomic_copy(source, paths.executable)
                signature = paths.executable.stat()
            except PermissionError as exc:
                raise RuntimeError("无法更新安装文件。请先从系统托盘退出 WOFO Login，再重新安装。") from exc
        _create_shortcuts(paths)
        _register_install(paths)
        migration_warning = ""
        try:
            _migrate_autorun(source, paths.executable)
        except OSError:
            # The installation is complete; leaving its EXE and registration
            # intact makes retry and manual recovery straightforward.
            migration_warning = "\n\n开机自启迁移未完成，请打开程序设置，重新关闭并开启“开机启动”。"
        _message(
            f"安装完成：\n{paths.executable}\n\n"
            "可从桌面或开始菜单启动。\n"
            "卸载时可使用 Windows“已安装的应用”、开始菜单中的“卸载 WOFO Login”，或程序设置中的卸载按钮。"
            + migration_warning
        )
        return SUCCESS
    except Exception as exc:
        if new_install and paths is not None:
            try:
                _cleanup_first_install(paths, new_shortcuts, signature)
            except Exception as cleanup_error:
                exc = RuntimeError(f"{exc}\n部分安装文件未能清理：{cleanup_error}\n可运行安装目录中的 WOFO Login.exe 重新安装。")
        _message(f"安装未完成：{exc}", error=True)
        return ERROR


def _uninstall_script(executable: Path, paths: AppPaths, *, registered: bool, delete_data: bool, process_id: int) -> str:
    """Produce a literal-path-only helper. It never recursively removes folders."""
    # Only a matching registration for this exact installed EXE permits touching
    # installer resources. A portable copy never removes another installation.
    installed = registered and _same_path(executable, paths.executable)
    links = [(paths.desktop_shortcut, ""), (paths.menu_shortcut, ""), (paths.uninstall_shortcut, "--uninstall")] if installed else []
    link_entries = ",\n".join(
        f"@{{ Path = {_quote_ps(path)}; Arguments = {_quote_ps(args)} }}" for path, args in links
    )
    data_entries = ", ".join(_quote_ps(paths.data_dir / name) for name in DATA_FILENAMES) if delete_data else ""
    owned_executables = ", ".join(_quote_ps(path) for path in _owned_executables(executable))
    registry_path = "HKCU:\\" + UNINSTALL_KEY
    autorun_path = "HKCU:\\" + AUTORUN_KEY
    # The helper independently rechecks registry and shortcut ownership so a
    # change between confirmation and exit cannot delete someone else's entry.
    return f"""
$ErrorActionPreference = 'Stop'
$exe = {_quote_ps(executable)}
$ownedExecutables = @({owned_executables})
$installDirectory = {_quote_ps(paths.install_dir)}
$dataDirectory = {_quote_ps(paths.data_dir)}
$registered = ${str(installed).lower()}
$deleteData = ${str(delete_data).lower()}
$links = @({link_entries})
$dataFiles = @({data_entries})
$registryPath = {_quote_ps(registry_path)}
$runPath = {_quote_ps(autorun_path)}
$expectedUninstall = {_quote_ps(_uninstall_command(executable))}
$shell = New-Object -ComObject WScript.Shell
try {{
    # A PID identifies only this caller; never stop processes by executable name.
    $caller = Get-Process -Id {int(process_id)} -ErrorAction SilentlyContinue
    if ($null -ne $caller -and -not $caller.WaitForExit(60000)) {{
        throw '程序尚未退出。请从托盘退出 WOFO Login 后重新卸载。'
    }}
    $removed = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {{
        try {{
            if (Test-Path -LiteralPath $exe) {{ Remove-Item -LiteralPath $exe -Force -ErrorAction Stop }}
            $removed = $true
            break
        }} catch {{ Start-Sleep -Milliseconds 500 }}
    }}
    if (-not $removed) {{ throw '文件仍被使用。请从托盘退出 WOFO Login 后重新卸载。' }}
    foreach ($name in @('校园网自动登录', 'WOFO Login')) {{
        $run = Get-ItemProperty -LiteralPath $runPath -Name $name -ErrorAction SilentlyContinue
        if ($null -ne $run) {{
            $command = [string]$run.$name
            foreach ($ownedExe in $ownedExecutables) {{
                if ($command -ieq $ownedExe -or $command -ieq ('"' + $ownedExe + '"') -or $command.StartsWith(('"' + $ownedExe + '" '), [System.StringComparison]::OrdinalIgnoreCase)) {{
                    Remove-ItemProperty -LiteralPath $runPath -Name $name -ErrorAction Stop
                    break
                }}
            }}
        }}
    }}
    if ($registered) {{
        foreach ($entry in $links) {{
            if (Test-Path -LiteralPath $entry.Path) {{
                $link = $shell.CreateShortcut($entry.Path)
                if ($link.TargetPath -ieq $exe -and $link.Arguments -eq $entry.Arguments) {{
                    Remove-Item -LiteralPath $entry.Path -Force -ErrorAction Stop
                }}
            }}
        }}
        $record = Get-ItemProperty -LiteralPath $registryPath -ErrorAction SilentlyContinue
        if ($null -ne $record -and $record.DisplayName -in @({_quote_ps(APP_NAME)}, {_quote_ps(APP_DISPLAY_NAME)}) -and $record.InstallLocation -ieq $installDirectory -and $record.UninstallString -ieq $expectedUninstall) {{
            Remove-Item -LiteralPath $registryPath -ErrorAction Stop
        }}
        foreach ($directory in @({_quote_ps(paths.menu_dir)}, $installDirectory)) {{
            if (Test-Path -LiteralPath $directory) {{
                # Directory.Delete(..., false) succeeds only for an empty directory.
                try {{ [System.IO.Directory]::Delete($directory, $false) }} catch {{ }}
            }}
        }}
    }}
    if ($deleteData) {{
        foreach ($dataFile in $dataFiles) {{
            if ([System.IO.Path]::GetDirectoryName($dataFile) -ine $dataDirectory) {{ throw '账号数据路径验证失败。' }}
            if (Test-Path -LiteralPath $dataFile -PathType Leaf) {{ Remove-Item -LiteralPath $dataFile -Force -ErrorAction Stop }}
        }}
        if (Test-Path -LiteralPath $dataDirectory) {{
            try {{ [System.IO.Directory]::Delete($dataDirectory, $false) }} catch {{ }}
        }}
    }}
    $dataMessage = if ($deleteData) {{ '已删除账号和设置。' }} else {{ '账号和设置已保留，重新使用时可以恢复。' }}
    $shell.Popup(('WOFO Login 已卸载。' + [Environment]::NewLine + $dataMessage), 0, 'WOFO Login', 64) | Out-Null
}} catch {{
    $shell.Popup(('卸载未完成：' + $_.Exception.Message), 0, 'WOFO Login', 16) | Out-Null
    exit 1
}}
"""


def _launch_uninstall_helper(script: str) -> None:
    flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    subprocess.Popen(
        _encoded_command(script), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, creationflags=flags,
    )


def run_uninstall() -> int:
    """Confirm, then schedule removal after the calling application exits."""
    try:
        executable = _frozen_executable()
        paths = _paths()
        registration = _read_registration()
        registered = _same_path(executable, paths.executable) and _registration_owned(registration, executable)
        mode = "已安装的程序" if registered else "当前便携版程序"
        if not _message(
            f"即将卸载{mode}：\n{executable}\n\n"
            "卸载会移除程序及其开机自启，账号和设置默认保留。\n"
            "从程序设置卸载时，当前窗口会自动退出。\n"
            "若从 Windows 或脚本卸载，请先从系统托盘退出正在运行的 WOFO Login。\n\n是否继续？", confirm=True,
        ):
            return CANCELED
        delete_data = _message(
            f"是否同时删除已保存的账号、密码密钥和设置？\n\n{paths.data_dir}\n\n"
            "选择“否”将保留数据，方便以后重新使用。\n选择“是”将永久删除已保存的账号和设置。", confirm=True,
        )
        script = _uninstall_script(
            executable, paths, registered=registered, delete_data=delete_data, process_id=os.getpid(),
        )
        _launch_uninstall_helper(script)
        return SUCCESS
    except Exception as exc:
        _message(f"卸载未开始：{exc}", error=True)
        return ERROR
