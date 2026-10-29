<div align="center">
  <img src="ui/assets/wofo-login.svg" width="96" alt="WOFO Login 图标">
</div>

# WOFO Login · 佛山大学校园网断线守护

[![Windows build](https://github.com/Hongtauo/WOFO/actions/workflows/build-windows.yml/badge.svg)](https://github.com/Hongtauo/WOFO/actions/workflows/build-windows.yml)
[![Ubuntu desktop build](https://github.com/Hongtauo/WOFO/actions/workflows/build-linux-gui.yml/badge.svg)](https://github.com/Hongtauo/WOFO/actions/workflows/build-linux-gui.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

一个面向 Dr.COM ePortal 的校园网连接工具。保存校园网账号后，WOFO Login 会在后台检测网络，并在断线时使用所选账号重新认证。

桌面版共用本地 WebView 界面：Windows 使用 WebView2，Ubuntu 22.04 使用 GTK 3 / WebKitGTK。macOS / Linux 另有独立的命令行版本。

## 功能

| 功能 | 说明 |
| --- | --- |
| 断线重连 | 检测间隔可设为 10～300 秒，使用所选校园网账号重新登录 |
| 账号管理 | 最多保存 5 个账号，可添加、编辑、删除，选择会在重启后保留 |
| 运营商选择 | 电信、移动、联通、校园网 |
| 后台运行 | 系统托盘提供恢复窗口、连接、登出、暂停守护和退出入口 |
| 运行日志 | 查看本次运行最近 500 条应用事件，支持清空 |
| 启动偏好 | 开机自启、启动时收起到托盘、系统通知 |
| 安装与卸载 | Windows 支持当前用户安装及卸载向导；Ubuntu 提供 `.deb` 和便携包 |

主动登出会暂停自动重连；退出应用会保留所选账号与守护偏好。界面资源随程序本地加载，不需要 Node.js 或在线 CDN。

## Windows 快速开始

### 获取程序

打开 [Windows 构建记录](https://github.com/Hongtauo/WOFO/actions/workflows/build-windows.yml)，选择成功的运行，下载 `WOFO-Login-Windows` 构建产物。解压其中的 `WOFO Login.zip` 后会得到：

```text
WOFO Login/
├── WOFO Login.exe
├── 安装.cmd
├── 卸载.cmd
└── 使用说明.txt
```

直接运行 `WOFO Login.exe` 即可。程序需要 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)；如果启动提示缺少运行时，请先安装它。

`安装.cmd` 是可选的：它会把程序安装到当前用户的 `%LOCALAPPDATA%/Programs/WOFO Login/`，创建桌面和开始菜单快捷方式，并添加 Windows「已安装的应用」入口，无需管理员权限。

### 配置连接

1. 连接校园网有线网络或 Wi-Fi。
2. 点击「添加账号」，填写校园网账号、密码和运营商。
3. 在账号列表中选择一个账号进行断线守护。
4. 点击「连接校园网」立即认证，或开启「断线守护」等待自动检测和重连。
5. 在「运行日志」中查看连接结果，在「偏好设置」中调整检测间隔和启动方式。

首次添加的账号会自动选中。编辑时密码留空可以保留原密码；账号达到 5 个后，需要先删除一个才能继续添加。

关闭窗口会收起到可用的系统托盘。要完全结束程序，请使用侧栏或托盘菜单中的「退出」。托盘不可用时，关闭窗口会直接退出。

### 卸载

在「偏好设置」中点击「卸载 WOFO Login」即可打开卸载向导。也可先从托盘退出主程序，再运行 `卸载.cmd`；安装版还支持从 Windows「已安装的应用」或开始菜单卸载。

卸载默认保留账号、密钥和设置。向导会单独询问是否删除这些数据。便携版卸载仅移除当前 EXE，不删除所在目录中的其他文件。

## Ubuntu 22.04 图形界面版

打开 [Ubuntu 桌面版构建记录](https://github.com/Hongtauo/WOFO/actions/workflows/build-linux-gui.yml)，选择成功的运行，下载 `WOFO-Login-Ubuntu-22.04-amd64` 构建产物。目标系统为 **Ubuntu 22.04 x86_64（amd64）**，需要桌面会话。

解压下载的构建产物后，在该目录安装：

```bash
sudo apt install ./WOFO-Login_1.0.0_ubuntu22.04_amd64.deb
wofo-login-gui
```

也可以从应用菜单打开「WOFO Login」。安装器会通过 Ubuntu 软件源安装 GTK、WebKitGTK 和 AppIndicator 运行库，无需自行安装 Python 或 Node.js。界面及账号操作与 Windows 版相同。

Ubuntu 默认桌面的 AppIndicator 扩展可显示托盘；其他桌面需要相应的托盘支持。托盘不可用时，窗口会保持可见，关闭窗口直接退出。登录自启使用用户级 `.desktop` 文件，不会创建系统服务。

### 便携运行与卸载

便携包仍需要系统图形库：

```bash
sudo apt install libgtk-3-0 libwebkit2gtk-4.0-37 \
  gir1.2-gtk-3.0 gir1.2-webkit2-4.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-freedesktop \
  libgirepository-1.0-1 libcairo2 libffi8 libsqlite3-0 libssl3 \
  libbz2-1.0 liblzma5 libexpat1 zlib1g fonts-noto-cjk
tar -xzf WOFO-Login_1.0.0_ubuntu22.04_amd64.tar.gz
./WOFO-Login/run.sh
```

卸载 `.deb` 安装版时先退出程序，再运行：

```bash
sudo apt remove wofo-login-gui
```

卸载保留账号和设置。便携版退出后删除解压的程序目录即可。两个版本均使用下方的数据目录。

## 账号数据与隐私

Windows 与 Ubuntu 桌面版均使用用户主目录下的 `.config/campus_login/`，更新程序无需迁移：

```text
%USERPROFILE%/.config/campus_login/
├── accounts.db
├── key.key
└── settings.json
```

Ubuntu 对应路径为 `~/.config/campus_login/`。

- 密码使用 Fernet 加密；密钥也保存在本机，请妥善保管整个数据目录。
- 页面状态不包含已保存的密码。编辑账号时，原密码不会回传到页面。
- 日志记录应用操作和连接结果，不展示密码及原始认证报文，关闭程序后清空。
- 源码仓库不包含真实账号、加密密钥、个人配置、构建缓存或本机诊断报告。

## macOS / Linux 命令行版

独立项目位于 [`campus-login-cli/`](campus-login-cli/README.md)，使用 Python 3.9+ 标准库，不依赖桌面界面、数据库或托盘。

```bash
cd campus-login-cli
python3 campus_login_cli.py status
python3 campus_login_cli.py login --user '学号' --service '电信'
python3 campus_login_cli.py daemon --user '学号' --service '电信' --interval 30
```

程序会交互提示输入密码。安装包、服务配置和卸载说明见 [CLI 文档](campus-login-cli/README.md)，已有平台发布包见 [Releases](https://github.com/Hongtauo/WOFO/releases)。

## 从源码运行桌面版

建议使用 Python 3.10+，Windows 构建流程使用 Python 3.11，Ubuntu 22.04 使用系统 Python 3.10。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Ubuntu 22.04 从源码运行：

```bash
sudo apt install python3.10-venv python3-gi python3-gi-cairo python3-cairo \
  gir1.2-gtk-3.0 gir1.2-webkit2-4.0 gir1.2-ayatanaappindicator3-0.1
/usr/bin/python3.10 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

`--system-site-packages` 让虚拟环境能使用 Ubuntu 提供的 GI 绑定。其他 Linux 发行版及 macOS 桌面版尚未验证，依赖说明见 [pywebview 文档](https://pywebview.flowrl.com/guide/installation)。

认证服务器和外网探测地址集中在 [`core/auth.py`](core/auth.py) 的 `CONFIG` 中。不同校园网络的认证配置可能不同；认证失败时，请先确认已接入相应网络以及账号、运营商选择正确。

## 开发与构建

```powershell
# 隐私检查、回归测试与前端语法检查
.\.venv\Scripts\python.exe scripts/check_publication.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s campus-login-cli/tests -v
node --check ui/web/app.js

# Windows EXE 与完整发布包
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist/webview "校园网守护.spec"
.\.venv\Scripts\python.exe scripts/package_release.py
```

生成的程序为 `dist/webview/WOFO Login.exe`，完整包为 `dist/WOFO Login.zip`。打包脚本会核对嵌入的源码、页面资源、8 档尺寸图标和 Windows 版本信息，再加入安装、卸载脚本及使用说明。

在 Ubuntu 22.04 上构建图形版：

```bash
sudo apt install python3.10-dev dpkg-dev desktop-file-utils libglib2.0-bin
PYTHON="$PWD/.venv/bin/python" bash scripts/build-linux-gui.sh
```

输出位于 `dist/linux/`，包含 `.deb`、便携 `.tar.gz` 和 SHA-256 校验文件。Linux 包使用系统 GTK / WebKitGTK，避免捆绑与宿主桌面冲突的图形库。请在目标 Ubuntu 版本上构建，Windows 无法直接交叉生成该程序。

Ubuntu 构建流程在 Xvfb 中启动真实 GTK 窗口，验证页面账号操作、设置、日志、图标和退出，再实际安装 `.deb` 并启动打包后的程序。截图和检查报告随构建产物提供。校园网认证仍需要在实际校园网络中验证。

测试使用临时数据和模拟网络，不读取真实账号或执行实际安装、卸载。提交前还可以检查准确的 Git 暂存内容：

```powershell
.\.venv\Scripts\python.exe scripts/check_publication.py --staged
```

主要目录：

```text
ui/                 WebView 界面、应用桥接、托盘和图标
core/               校园网认证与密码加密
database/           SQLite 账号存储
config/             用户偏好与开机自启
app_lifecycle.py    Windows 安装和卸载
scripts/            图标生成、隐私检查、打包与校验
packaging/linux/    Ubuntu 桌面入口、依赖和 PyInstaller hooks
tests/              桌面版离线回归测试
campus-login-cli/   macOS / Linux 独立命令行版
.github/workflows/  Windows、Ubuntu 桌面版和 CLI 构建流程
```

## 许可证

本项目采用 [MIT License](LICENSE)。请仅使用本人已获授权的账号进行正常校园网认证。
