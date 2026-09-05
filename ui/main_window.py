"""
主窗口模块
现代化深色主题 UI
"""

import logging
import threading
from typing import Optional

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.dialogs import Messagebox

from core.auth import login, logout, check_network, daemon_mode, stop_daemon
from database.db import get_db, Account
from config.settings import get_settings

log = logging.getLogger("campus_login.ui")


class AddAccountDialog:
    """添加/编辑账号对话框"""

    def __init__(self, parent, account: Account = None):
        self.account = account
        self.result = None
        self._show_password = False
        self.dialog = ttk.Toplevel(parent)
        self.dialog.title("编辑账号" if account else "添加账号")
        self.dialog.geometry("400x280")
        self.dialog.minsize(400, 280)
        self.dialog.maxsize(400, 280)
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 账号
        ttk.Label(self.dialog, text="学号:").place(x=30, y=30)
        self.user_id_var = ttk.StringVar(value=account.user_id if account else "")
        ttk.Entry(self.dialog, textvariable=self.user_id_var, width=25).place(x=100, y=28)

        # 密码
        ttk.Label(self.dialog, text="密码:").place(x=30, y=70)

        # 密码输入框容器
        pwd_frame = ttk.Frame(self.dialog, width=280, height=30)
        pwd_frame.place(x=100, y=68)
        pwd_frame.pack_propagate(False)
        self.password_var = ttk.StringVar(
            value=account.password if (account and account.password) else ""
        )
        self.password_entry = ttk.Entry(pwd_frame, textvariable=self.password_var, show="*", width=20)
        self.password_entry.pack(side=LEFT, fill=X, expand=True)
        self.toggle_btn = ttk.Button(
            pwd_frame, text="显示", width=5, command=self._toggle_password,
            bootstyle="secondary"
        )
        self.toggle_btn.pack(side=RIGHT)

        # 运营商
        ttk.Label(self.dialog, text="运营商:").place(x=30, y=110)
        self.service_var = ttk.StringVar(value=account.service if account else "电信")
        services = ["电信", "移动", "联通", "校园网"]
        ttk.Combobox(self.dialog, textvariable=self.service_var, values=services, state="readonly", width=22).place(x=100, y=108)

        # 设为默认
        self.is_default_var = ttk.BooleanVar(value=account.is_default if account else True)
        ttk.Checkbutton(self.dialog, text="设为默认账号", variable=self.is_default_var).place(x=100, y=145)

        # 按钮
        ttk.Button(self.dialog, text="取消", command=self._on_cancel, bootstyle="secondary", width=10).place(x=160, y=200)
        ttk.Button(self.dialog, text="确定", command=self._on_ok, bootstyle="primary", width=10).place(x=270, y=200)

        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # 延迟居中显示，确保窗口完全渲染后再居中
        self.dialog.after(100, self._center_dialog)

    def _center_dialog(self):
        """延迟居中窗口"""
        screen_width = self.dialog.winfo_screenwidth()
        screen_height = self.dialog.winfo_screenheight()
        x = (screen_width - 400) // 2
        y = (screen_height - 280) // 2
        self.dialog.geometry(f"400x280+{x}+{y}")

    def _toggle_password(self):
        """切换密码显示/隐藏"""
        self._show_password = not self._show_password
        if self._show_password:
            self.password_entry.configure(show="")
            self.toggle_btn.configure(text="隐藏")
        else:
            self.password_entry.configure(show="*")
            self.toggle_btn.configure(text="显示")

    def _on_ok(self):
        user_id = self.user_id_var.get().strip()
        password = self.password_var.get()
        service = self.service_var.get()
        is_default = self.is_default_var.get()

        if not user_id or not password:
            Messagebox.show_error("学号和密码不能为空", "输入错误")
            return

        self.result = {
            "user_id": user_id,
            "password": password,
            "service": service,
            "is_default": is_default
        }
        self.dialog.destroy()

    def _on_cancel(self):
        self.dialog.destroy()


class SettingsDialog:
    """设置对话框"""

    def __init__(self, parent):
        self.settings = get_settings()
        self.result = None

        self.dialog = ttk.Toplevel(parent)
        self.dialog.title("设置")
        self.dialog.geometry("300x250")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 居中显示
        self.dialog.update_idletasks()
        parent = self.dialog.master
        screen_width = parent.winfo_screenwidth()
        screen_height = parent.winfo_screenheight()
        x = parent.winfo_x() + (parent.winfo_width() - 300) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 250) // 2
        self.dialog.geometry(f"300x250+{x}+{y}")

        frame = ttk.Frame(self.dialog, padding=20)
        frame.pack(fill=BOTH, expand=YES)

        # 开机自启
        self.auto_start_var = ttk.BooleanVar(value=self.settings.get().auto_start)
        ttk.Checkbutton(frame, text="开机自动启动", variable=self.auto_start_var).pack(anchor=W, pady=5)

        # 启动时最小化
        self.start_minimized_var = ttk.BooleanVar(value=self.settings.get().start_minimized)
        ttk.Checkbutton(frame, text="启动时最小化到托盘", variable=self.start_minimized_var).pack(anchor=W, pady=5)

        # 显示通知
        self.show_notifications_var = ttk.BooleanVar(value=self.settings.get().show_notifications)
        ttk.Checkbutton(frame, text="显示系统通知", variable=self.show_notifications_var).pack(anchor=W, pady=5)

        # 检测间隔
        ttk.Label(frame, text="检测间隔 (秒):").pack(anchor=W, pady=(15, 5))
        self.interval_var = ttk.IntVar(value=self.settings.get().check_interval)
        ttk.Spinbox(frame, from_=10, to=300, textvariable=self.interval_var, width=10).pack(anchor=W, pady=5)

        # 按钮
        btn_frame = ttk.Frame(self.dialog)
        btn_frame.pack(fill=X, pady=10, padx=10)
        ttk.Button(btn_frame, text="取消", command=self._on_cancel, bootstyle="secondary", width=10).pack(side=RIGHT, padx=5)
        ttk.Button(btn_frame, text="保存", command=self._on_ok, bootstyle="primary", width=10).pack(side=RIGHT)

        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_ok(self):
        self.result = {
            "auto_start": self.auto_start_var.get(),
            "start_minimized": self.start_minimized_var.get(),
            "show_notifications": self.show_notifications_var.get(),
            "check_interval": self.interval_var.get()
        }
        self.dialog.destroy()

    def _on_cancel(self):
        self.dialog.destroy()


class MainWindow:
    """主窗口"""

    def __init__(self):
        # 创建主窗口
        self.root = ttk.Window(themename="superhero")
        self.root.title("校园网自动登录")
        self.root.geometry("450x500")
        self.root.resizable(False, False)

        # 数据
        self.db = get_db()
        self.settings = get_settings()
        self.selected_account: Optional[Account] = None
        self._daemon = None

        # UI 变量
        self.status_var = ttk.StringVar(value="未连接")

        self._setup_ui()
        self._center_window()
        self._setup_tray()
        self._refresh_accounts()
        self._start_status_check()
        self._init_daemon()

        # 窗口事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _init_daemon(self):
        """初始化守护模式"""
        settings = self.settings.get()
        if settings.daemon_enabled and self.db.get_default_account():
            self.root.after(100, self._start_daemon)

    def _center_window(self, win=None):
        """窗口居中显示"""
        window = win or self.root
        window.update_idletasks()
        width = window.winfo_width()
        height = window.winfo_height()
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _setup_ui(self):
        """设置 UI"""
        # 顶部标题栏
        header = ttk.Frame(self.root, padding=15)
        header.pack(fill=X)

        ttk.Label(
            header, text="校园网自动登录",
            font=("Segoe UI", 16, "bold")
        ).pack(side=LEFT)

        # 设置按钮
        ttk.Button(
            header, text="⚙", width=3, command=self._open_settings,
            bootstyle="secondary"
        ).pack(side=RIGHT)

        # 状态显示
        status_frame = ttk.Frame(self.root, padding=10)
        status_frame.pack(fill=X)

        status_indicator = ttk.Frame(status_frame, width=12, height=12)
        status_indicator.pack(side=LEFT, padx=(10, 10))
        status_indicator.pack_propagate(False)

        self.status_canvas = ttk.Canvas(status_indicator, width=12, height=12, bg="#F44336", highlightthickness=0)
        self.status_canvas.create_oval(1, 1, 11, 11, fill="#F44336", outline="")
        self.status_canvas.pack(fill=BOTH, expand=YES)

        ttk.Label(
            status_frame, textvariable=self.status_var,
            font=("Segoe UI", 11)
        ).pack(side=LEFT)

        # 账号列表
        list_frame = ttk.LabelFrame(self.root, text="账号列表")
        list_frame.pack(fill=BOTH, expand=YES, padx=15, pady=10)

        # 内层容器用于 padding
        list_inner = ttk.Frame(list_frame, padding=10)
        list_inner.pack(fill=BOTH, expand=YES)

        # 列表框
        list_container = ttk.Frame(list_inner)
        list_container.pack(fill=BOTH, expand=YES)

        self.account_listbox = ttk.Treeview(
            list_container, columns=("service", "default"), show="tree headings",
            height=5, selectmode="browse"
        )
        self.account_listbox.heading("#0", text="学号")
        self.account_listbox.heading("service", text="运营商")
        self.account_listbox.heading("default", text="默认")
        self.account_listbox.column("#0", width=150)
        self.account_listbox.column("service", width=80)
        self.account_listbox.column("default", width=50)

        scrollbar = ttk.Scrollbar(list_container, orient=VERTICAL, command=self.account_listbox.yview)
        self.account_listbox.configure(yscrollcommand=scrollbar.set)

        self.account_listbox.pack(side=LEFT, fill=BOTH, expand=YES)
        scrollbar.pack(side=RIGHT, fill=Y)

        self.account_listbox.bind("<<TreeviewSelect>>", self._on_account_select)

        # 账号操作按钮
        btn_frame = ttk.Frame(list_inner)
        btn_frame.pack(fill=X, pady=(10, 0))

        ttk.Button(
            btn_frame, text="+ 添加", command=self._add_account,
            bootstyle="success", width=10
        ).pack(side=LEFT, padx=2)
        ttk.Button(
            btn_frame, text="编辑", command=self._edit_account,
            bootstyle="info", width=8
        ).pack(side=LEFT, padx=2)
        ttk.Button(
            btn_frame, text="删除", command=self._delete_account,
            bootstyle="danger", width=8
        ).pack(side=LEFT, padx=2)
        ttk.Button(
            btn_frame, text="设为默认", command=self._set_default,
            bootstyle="secondary", width=10
        ).pack(side=RIGHT, padx=2)

        # 底部操作区
        action_frame = ttk.Frame(self.root, padding=15)
        action_frame.pack(fill=X)

        self.login_btn = ttk.Button(
            action_frame, text="登录", command=self._do_login,
            bootstyle="success", width=15
        )
        self.login_btn.pack(side=LEFT, padx=5)

        self.logout_btn = ttk.Button(
            action_frame, text="登出", command=self._do_logout,
            bootstyle="warning", width=15
        )
        self.logout_btn.pack(side=LEFT, padx=5)

        self.daemon_btn = ttk.Button(
            action_frame, text="开启守护", command=self._toggle_daemon,
            bootstyle="info", width=15
        )
        self.daemon_btn.pack(side=RIGHT, padx=5)

    def _setup_tray(self):
        """设置托盘"""
        from ui.tray import TrayIcon

        self.tray = TrayIcon(
            parent=self.root,
            on_login=self._do_login,
            on_logout=self._do_logout,
            on_show=self._show_window,
            on_quit=self._quit_app,
            on_toggle_daemon=self._toggle_daemon
        )

    def _refresh_accounts(self):
        """刷新账号列表"""
        for item in self.account_listbox.get_children():
            self.account_listbox.delete(item)

        accounts = self.db.get_all_accounts()
        for acc in accounts:
            default_text = "✓" if acc.is_default else ""
            self.account_listbox.insert(
                "", "end",
                text=acc.user_id,
                values=(acc.service, default_text),
                tags=(str(acc.id),)
            )

            if acc.is_default:
                self.selected_account = acc
                if acc.decrypt_error:
                    user_id = acc.user_id
                    self.root.after(500, lambda uid=user_id: Messagebox.show_warning(
                        f"默认账号 {uid} 的密码数据损坏，"
                        "请重新编辑该账号的密码。",
                        "密码损坏"
                    ))

    def _on_account_select(self, event):
        """选择账号"""
        selection = self.account_listbox.selection()
        if selection:
            item = self.account_listbox.item(selection[0])
            tags = item.get("tags", [])
            if tags:
                account_id = int(tags[0])
                self.selected_account = self.db.get_account(account_id)
                if self.selected_account and self.selected_account.decrypt_error:
                    Messagebox.show_warning(
                        f"账号 {self.selected_account.user_id} 的密码数据损坏，"
                        "请重新编辑该账号的密码。",
                        "密码损坏"
                    )

    def _add_account(self):
        """添加账号"""
        dialog = AddAccountDialog(self.root)
        self.root.wait_window(dialog.dialog)

        if dialog.result:
            result = dialog.result
            if self.db.add_account(
                result["user_id"],
                result["password"],
                result["service"],
                result["is_default"]
            ):
                self._refresh_accounts()
                Messagebox.show_info("账号添加成功", "成功")
            else:
                Messagebox.show_error("账号添加失败，学号可能已存在", "错误")

    def _edit_account(self):
        """编辑账号"""
        if not self.selected_account:
            Messagebox.show_warning("请先选择要编辑的账号", "提示")
            return

        dialog = AddAccountDialog(self.root, self.selected_account)
        self.root.wait_window(dialog.dialog)

        if dialog.result:
            result = dialog.result
            if self.db.update_account(
                self.selected_account.id,
                result["user_id"],
                result["password"],
                result["service"],
                result["is_default"]
            ):
                self._refresh_accounts()
                Messagebox.show_info("账号更新成功", "成功")
            else:
                Messagebox.show_error("账号更新失败", "错误")

    def _delete_account(self):
        """删除账号"""
        if not self.selected_account:
            Messagebox.show_warning("请先选择要删除的账号", "提示")
            return

        if Messagebox.showyesno(
            f"确定要删除账号 {self.selected_account.user_id} 吗？",
            "确认删除"
        ):
            if self.db.delete_account(self.selected_account.id):
                self._refresh_accounts()
                self.selected_account = None

    def _set_default(self):
        """设为默认"""
        if not self.selected_account:
            Messagebox.show_warning("请先选择要设为默认的账号", "提示")
            return

        if self.db.set_default(self.selected_account.id):
            self._refresh_accounts()
            Messagebox.show_info("已设为默认账号", "成功")

    def _do_login(self):
        """执行登录"""
        account = self.selected_account or self.db.get_default_account()
        if not account:
            Messagebox.show_warning("请先添加账号或选择要登录的账号", "提示")
            return

        if account.decrypt_error or account.password is None:
            Messagebox.show_error(
                f"账号 {account.user_id} 的密码数据损坏，无法登录。请重新编辑该账号。",
                "密码损坏"
            )
            return

        self.login_btn.config(state=DISABLED)
        self.status_var.set("正在登录...")

        def do():
            success = login(account.user_id, account.password, account.service)
            self.root.after(0, lambda: self._on_login_result(success))

        threading.Thread(target=do, daemon=True).start()

    def _on_login_result(self, success: bool):
        """登录结果回调"""
        self.login_btn.config(state=NORMAL)
        if success:
            self._update_status(True)
            if self.settings.get().show_notifications:
                Messagebox.show_info("登录成功", "校园网")
        else:
            self._update_status(False)
            Messagebox.show_error("登录失败，请检查网络连接", "错误")

    def _do_logout(self):
        """执行登出"""
        self.logout_btn.config(state=DISABLED)
        self.status_var.set("正在登出...")

        def do():
            success = logout()
            self.root.after(0, lambda: self._on_logout_result(success))

        threading.Thread(target=do, daemon=True).start()

    def _on_logout_result(self, success: bool):
        """登出结果回调"""
        self.logout_btn.config(state=NORMAL)
        if success:
            self._update_status(False)
        else:
            Messagebox.show_warning("登出操作可能未成功", "提示")

    def _toggle_daemon(self):
        """切换守护模式"""
        if self._daemon:
            self._stop_daemon()
        else:
            self._start_daemon()

    def _start_daemon(self):
        """启动守护模式"""
        account = self.db.get_default_account()
        if not account:
            return

        settings = self.settings.get()
        self._daemon = daemon_mode(self.db, settings.check_interval)
        self._daemon.set_status_callback(self._schedule_status_update)
        self.daemon_btn.config(text="关闭守护", bootstyle="danger")
        self.tray.set_daemon_status(True)
        self.settings.update(daemon_enabled=True)
        log.info("守护模式已启动")

    def _stop_daemon(self):
        """停止守护模式"""
        if self._daemon:
            stop_daemon()
            self._daemon = None
        self.daemon_btn.config(text="开启守护", bootstyle="info")
        self.tray.set_daemon_status(False)
        self.settings.update(daemon_enabled=False)
        log.info("守护模式已停止")

    def _start_status_check(self):
        """启动状态检查"""
        def schedule_next():
            self.root.after(5000, run_check)

        def run_check():
            def do():
                connected = check_network()
                self._schedule_status_update(connected)
                try:
                    self.root.after(0, schedule_next)
                except Exception:
                    pass

            threading.Thread(target=do, daemon=True).start()

        run_check()

    def _schedule_status_update(self, connected: bool):
        """从后台线程安全调度 UI 状态更新。"""
        try:
            self.root.after(0, lambda: self._update_status(connected))
        except Exception as e:
            log.debug(f"状态更新调度失败: {e}")

    def _update_status(self, connected: bool):
        """更新状态显示"""
        color = "#4CAF50" if connected else "#F44336"
        self.status_var.set("已连接" if connected else "未连接")
        self.status_canvas.configure(bg=color)
        self.status_canvas.delete("all")
        self.status_canvas.create_oval(1, 1, 11, 11, fill=color, outline="")

    def _open_settings(self):
        """打开设置对话框"""
        dialog = SettingsDialog(self.root)
        self.root.wait_window(dialog.dialog)

        if dialog.result:
            self.settings.update(**dialog.result)
            Messagebox.show_info("设置已保存", "成功")

    def _on_close(self):
        """关闭窗口"""
        settings = self.settings.get()
        if settings.start_minimized and self.tray._available:
            try:
                self.tray.minimize_to_tray()
            except Exception as e:
                log.error(f"托盘最小化失败: {e}")
                self._quit_app()
        else:
            self._quit_app()

    def _show_window(self):
        """显示窗口"""
        self.tray.restore_from_tray()

    def _quit_app(self):
        """退出程序"""
        if self._daemon:
            self._stop_daemon()
        if self.tray._available:
            self.tray.hide()
        self.root.quit()

    def run(self):
        """运行主窗口"""
        self.root.mainloop()
