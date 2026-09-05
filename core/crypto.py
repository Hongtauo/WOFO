"""
加密工具模块
使用 cryptography 库的 Fernet 对称加密
"""

import os
import base64
import hashlib
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# 配置目录
CONFIG_DIR = Path.home() / ".config" / "campus_login"
KEY_FILE = CONFIG_DIR / "key.key"


def _ensure_config_dir():
    """确保配置目录存在"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _get_or_create_key() -> bytes:
    """
    获取或创建加密密钥
    首次创建时生成并保存到本地
    """
    _ensure_config_dir()

    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()

    # 生成新密钥
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    return key


def _derive_key_from_machine() -> bytes:
    """
    从机器特征派生密钥（用于本地加密存储）
    这确保了密钥与本机绑定
    """
    # 使用机器特征生成盐
    machine_info = f"{os.environ.get('COMPUTERNAME', 'default')}-{os.environ.get('USERNAME', 'user')}"
    salt = hashlib.sha256(machine_info.encode()).digest()[:16]

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )

    # 派生一个可用的 Fernet 密钥
    derived = kdf.derive(b"campus_login_master_key")
    return base64.urlsafe_b64encode(derived)


class CryptoManager:
    """加密管理器"""

    def __init__(self):
        self._fernet: Optional[Fernet] = None
        self._initialize()

    def _initialize(self):
        """初始化加密器"""
        try:
            # 优先使用保存的密钥
            key = _get_or_create_key()
            self._fernet = Fernet(key)
        except Exception:
            # 降级到机器派生密钥
            key = _derive_key_from_machine()
            self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> bytes:
        """加密字符串"""
        if not self._fernet:
            self._initialize()
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        """解密字符串"""
        if not self._fernet:
            self._initialize()
        return self._fernet.decrypt(ciphertext).decode()


# 全局加密管理器
_crypto: Optional[CryptoManager] = None


def get_crypto() -> CryptoManager:
    """获取加密管理器（单例）"""
    global _crypto
    if _crypto is None:
        _crypto = CryptoManager()
    return _crypto
