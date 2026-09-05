# Campus Login CLI

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

面向 macOS 和 Linux 的独立 Dr.COM ePortal 命令行客户端。认证协议、RSA
密码加密、联网检测和断线重连均只使用 Python 标准库，不依赖原项目的 GUI、
数据库、Tk、托盘或 Windows API。

## 环境

- Python 3.9+
- 已连接到校园网（有线或 Wi-Fi）
- 默认认证门户：`http://10.10.9.4`

## 直接运行

```bash
cd campus-login-cli
python3 campus_login_cli.py status
python3 campus_login_cli.py login --user '学号' --service '电信'
python3 campus_login_cli.py daemon --user '学号' --service '电信' --interval 30
python3 campus_login_cli.py logout
```

登录时会在终端安全地提示输入密码，输入内容不会回显。守护模式按 `Ctrl+C`
停止。

## 安装为系统命令

```bash
cd campus-login-cli
python3 -m pip install .
campus-login status
campus-login login --user '学号' --service '电信'
```

建议在虚拟环境或通过 `pipx install .` 安装。

## 无 Python 环境安装（Debian / Ubuntu）

发布的 `.deb` 已把 Python 运行时和程序一起封装，目标电脑不需要安装 Python：

```bash
sudo apt install ./campus-login_1.0.0_amd64.deb
campus-login status
campus-login login --user '学号' --service '电信'
```

ARM64 Linux（例如部分开发板）使用 `campus-login_1.0.0_arm64.deb`。

配置开机启动和断线重连：

```bash
sudo editor /etc/default/campus-login
sudo systemctl enable --now campus-login
systemctl status campus-login
journalctl -u campus-login -f
```

`/etc/default/campus-login` 以 `0600` 权限安装，其中需要填写账号和密码。卸载：

```bash
sudo apt remove campus-login
```

直接执行 `apt install campus-login` 还需要将 `.deb` 发布到一个签名的 APT 软件源；
本项目当前提供的是可由 `apt install ./文件.deb` 安装的标准 Debian 包。

## 非交互运行

后台服务没有交互终端，需要从环境变量读取密码：

```bash
CAMPUS_LOGIN_PASSWORD='你的密码' campus-login daemon --user '学号'
```

密码不会作为命令行参数出现，因此不会暴露在 `ps` 的命令列表中。仍应限制保存
环境变量的配置文件权限，例如执行 `chmod 600 文件名`。

## 更换门户地址

```bash
campus-login --portal 'http://portal.example.edu' login --user '学号'
```

也可设置 `CAMPUS_LOGIN_PORTAL` 环境变量。`--portal` 等全局选项应写在
`login/status/daemon/logout` 子命令之前。

## 退出码

- `0`：操作成功或网络在线
- `1`：操作失败或网络离线
- `2`：参数或配置错误

## 打包为单文件（可选）

PyInstaller 不支持交叉编译，请分别在 macOS 和 Linux 上构建：

```bash
python3 -m pip install pyinstaller
python3 -m PyInstaller --onefile --name campus-login campus_login_cli.py
./dist/campus-login status
```

也可以使用已经准备好的脚本：

```bash
# 必须在 Debian/Ubuntu 构建机上执行，生成 dist/*.deb
python3 -m pip install pyinstaller
sudo apt install dpkg-dev
./build-linux-deb.sh

# 必须在 macOS 构建机上执行，生成独立可执行文件 dist/campus-login
python3 -m pip install pyinstaller
./build-macos.sh
```

仓库中的 `.github/workflows/build-cli-packages.yml` 可以在推送 `cli-v*` 标签或
手动触发时构建 Linux AMD64、Linux ARM64 的 `.deb` 和 macOS ARM64 独立程序。

## 许可与合规

本项目采用 [MIT License](../LICENSE)。仅应用于用户已获授权使用的网络，
使用者需自行遵守学校和网络运营方的规章。
