"""
数据库模块
SQLite 存储账号信息，密码加密
"""

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.crypto import get_crypto

log = logging.getLogger("campus_login.database")

# 数据库路径
DB_PATH = Path.home() / ".config" / "campus_login" / "accounts.db"


@dataclass
class Account:
    """账号数据结构"""
    id: Optional[int]
    user_id: str
    password: Optional[str]  # 解密后的密码；无法解密时为 None
    service: str
    is_default: bool
    created_at: str
    decrypt_error: bool = False  # 密码解密是否失败


class Database:
    """数据库管理器"""

    def __init__(self, db_path: Path = None):
        self.db_path = Path(db_path) if db_path is not None else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._initialize()

    def _initialize(self):
        """初始化数据库连接和表"""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                encrypted_password BLOB NOT NULL,
                service TEXT DEFAULT '电信',
                is_default INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def add_account(self, user_id: str, password: str, service: str = "电信", set_default: bool = False) -> bool:
        """添加账号"""
        with self._lock:
            encrypted_password = get_crypto().encrypt(password)
            try:
                # 默认标记和账号写入必须一起提交，失败时一起回滚。
                with self._conn:
                    if set_default:
                        self._conn.execute("UPDATE accounts SET is_default = 0")
                    self._conn.execute(
                        """INSERT INTO accounts (user_id, encrypted_password, service, is_default, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (user_id, encrypted_password, service, 1 if set_default else 0,
                         datetime.now().isoformat())
                    )
                return True
            except sqlite3.IntegrityError:
                return False

    def update_account(self, account_id: int, user_id: str = None, password: str = None,
                       service: str = None, is_default: bool = None) -> bool:
        """更新账号"""
        with self._lock:
            updates = []
            params = []
            if user_id is not None:
                updates.append("user_id = ?")
                params.append(user_id)
            if password is not None:
                updates.append("encrypted_password = ?")
                params.append(get_crypto().encrypt(password))
            if service is not None:
                updates.append("service = ?")
                params.append(service)
            if is_default is not None:
                updates.append("is_default = ?")
                params.append(1 if is_default else 0)
            if not updates:
                return False
            try:
                with self._conn:
                    if not self._conn.execute(
                        "SELECT 1 FROM accounts WHERE id = ?", (account_id,)
                    ).fetchone():
                        return False
                    if is_default:
                        self._conn.execute("UPDATE accounts SET is_default = 0")
                    params.append(account_id)
                    cursor = self._conn.execute(
                        f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?", params
                    )
                return cursor.rowcount > 0
            except sqlite3.IntegrityError:
                return False

    def delete_account(self, account_id: int) -> bool:
        """删除账号"""
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            return cursor.rowcount > 0

    def get_account(self, account_id: int) -> Optional[Account]:
        """获取单个账号"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            return self._row_to_account(row) if row else None

    def get_all_accounts(self) -> list[Account]:
        """获取所有账号"""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
            return [self._row_to_account(row) for row in rows]

    def get_default_account(self) -> Optional[Account]:
        """获取默认账号"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE is_default = 1 LIMIT 1"
            ).fetchone()
            if not row:
                row = self._conn.execute(
                    "SELECT * FROM accounts ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            return self._row_to_account(row) if row else None

    def set_default(self, account_id: int) -> bool:
        """设为默认账号"""
        with self._lock, self._conn:
            if not self._conn.execute(
                "SELECT 1 FROM accounts WHERE id = ?", (account_id,)
            ).fetchone():
                return False
            self._conn.execute("UPDATE accounts SET is_default = 0")
            cursor = self._conn.execute(
                "UPDATE accounts SET is_default = 1 WHERE id = ?", (account_id,)
            )
            return cursor.rowcount > 0

    def _row_to_account(self, row: sqlite3.Row) -> Optional[Account]:
        """将数据库行转换为 Account 对象"""
        if not row:
            return None

        decrypt_error = False
        crypto = get_crypto()
        try:
            password = crypto.decrypt(row["encrypted_password"])
        except Exception as e:
            log.error(f"账号 {row['user_id']} 密码解密失败: {e}")
            password = None
            decrypt_error = True

        return Account(
            id=row["id"],
            user_id=row["user_id"],
            password=password,
            service=row["service"],
            is_default=bool(row["is_default"]),
            created_at=row["created_at"],
            decrypt_error=decrypt_error,
        )

    def close(self):
        """关闭数据库连接"""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


# 全局数据库实例
_db: Optional[Database] = None
_db_lock = threading.Lock()


def get_db() -> Database:
    """获取数据库实例（单例）"""
    global _db
    with _db_lock:
        if _db is None:
            _db = Database()
        return _db
