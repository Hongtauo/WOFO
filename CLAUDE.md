# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WOFO Login · 佛山大学校园网断线守护 — Dr.COM ePortal 客户端，Windows 使用 pywebview，macOS / Linux 的独立命令行版本位于 campus-login-cli/。

## Running

```bash
pip install -r requirements.txt
python main.py
```

## Project Structure

```
WOFO/
├── core/               # 核心逻辑
│   ├── auth.py         # 登录/登出/守护模式
│   └── crypto.py       # 密码加密
├── database/
│   └── db.py           # SQLite 数据库
├── config/
│   └── settings.py     # 设置与开机自启
├── ui/
│   ├── main_window.py  # pywebview 窗口和生命周期
│   ├── api.py          # JS/Python 桥接和串行网络任务
│   ├── web/            # 离线 HTML/CSS/JS 界面
│   ├── assets/         # 统一图标资源
│   └── tray.py         # 系统托盘
├── requirements.txt
└── main.py             # 入口
```

## Key Configuration

`core/auth.py` 中的 CONFIG 字典配置 portal 服务器地址和检测参数。

## Data Storage

用户数据存储在 `~/.config/campus_login/` 目录下，不在代码仓库中。

发布前运行 `python scripts/check_publication.py`；暂存后使用 `--staged` 检查实际提交内容。不要提交环境、构建目录、崩溃报告、数据库、密钥或个人配置。
