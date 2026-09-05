# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

校园网自动登录 (Campus Network Auto Login) - 基于 Dr.COM ePortal 的校园网自动登录工具，支持账号管理、守护模式和系统托盘。

## Running

```bash
pip install -r requirements.txt
python main.py
```

## Project Structure

```
校园网自动登录/
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

## Key Configuration

`core/auth.py` 中的 CONFIG 字典配置 portal 服务器地址和检测参数。

## Data Storage

用户数据存储在 `~/.config/campus_login/` 目录下，不在代码仓库中。
