# 校园网自动登录器

基于 Dr.COM ePortal 的校园网自动登录工具，支持账号管理、守护模式和系统托盘。

## 功能特性

- 账号管理：添加、编辑、删除、设置默认账号
- 密码加密存储：使用 Fernet 对称加密
- 守护模式：断线自动重连
- 系统托盘：最小化后台运行，支持双击打开
- 开机自启：支持 Windows/macOS/Linux
- 跨平台支持

## 安装

```bash
pip install -r requirements.txt
```

依赖：
- `cryptography` - 密码加密
- `ttkbootstrap` - UI 界面
- `pystray` - 系统托盘
- `Pillow` - 托盘图标

## macOS / Linux（核心版）

独立的纯命令行项目位于 [`campus-login-cli/`](campus-login-cli/README.md)。
它不导入本项目的 GUI、数据库或托盘代码，仅依赖 Python 3.9+
标准库，可以整个目录单独复制到 macOS 或 Linux。

```bash
cd campus-login-cli
python3 campus_login_cli.py login --user "学号" --service "电信"
```

## 图形界面

```bash
pip install -r requirements.txt
python main.py
```

## 项目结构

```
校园网自动登录/
├── campus-login-cli/   # macOS/Linux 独立纯命令行版
├── core/               # 核心逻辑
│   ├── auth.py         # 登录/登出/守护模式
│   └── crypto.py       # 密码加密
├── database/
│   └── db.py           # SQLite 数据库
├── config/
│   └── settings.py     # 设置与开机自启
├── ui/
│   ├── main_window.py  # 主界面
│   └── tray.py         # 系统托盘
├── requirements.txt
└── main.py             # 入口
```

## 数据存储

配置和数据存储在 `~/.config/campus_login/` 目录下：
- `accounts.db` - 加密的账号数据库
- `key.key` - 加密密钥
- `settings.json` - 用户设置

## 使用说明

1. 首次运行，点击「添加」添加校园网账号
2. 选择运营商（电信/移动/联通/校园网）
3. 点击「登录」或勾选「设为默认账号」
4. 开启「守护模式」可实现断线自动重连
5. 关闭窗口会最小化到系统托盘
6. 在设置中可开启「开机自动启动」

## 界面预览

- 深色主题（superhero）
- 状态指示灯（绿色=已连接，红色=未连接，绿色+循环=守护模式）
- 系统托盘图标

## 打包

PyInstaller 不支持交叉编译：Windows `.exe`、macOS `.app` 和 Linux 可执行文件
必须分别在对应系统上构建。

### macOS / Linux 核心命令行版

```bash
python3 -m pip install pyinstaller
cd campus-login-cli
python3 -m PyInstaller --onefile --name campus-login campus_login_cli.py
./dist/campus-login status
```

### Windows GUI

### 方式一：命令行打包

```bash
pyinstaller --onefile --windowed --name "校园网自动登录" main.py
```

参数说明：
- `--onefile`：打包为单个 exe 文件
- `--windowed`：无控制台窗口（GUI 模式）
- `--name`：输出文件名

打包完成后，exe 文件在 `dist/` 目录下。

### 方式二：使用 spec 文件（推荐）

创建 `campus_login.spec` 文件：

```python
# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['cryptography', 'tkinter', 'pystray', 'PIL'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='校园网自动登录',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

然后运行：

```bash
pyinstaller campus_login.spec
```

### 常见问题

1. **打包后运行报错**：确保安装所有依赖后重新打包
2. **图标不显示**：Windows exe 图标需要 .ico 格式文件
3. **托盘功能异常**：可能是 pystray 打包问题，可尝试添加 `hiddenimports`
