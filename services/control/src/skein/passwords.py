"""本地账号的口令散列：scrypt。

选 scrypt 而不是 argon2：它在标准库 ``hashlib`` 里，控制面不必为一个函数引入带原生
扩展的依赖；它是内存困难算法（RFC 7914），对显卡与专用硬件的离线爆破有足够的抬升；
OWASP 把 scrypt 与 argon2id 并列为可接受的口令散列。参数 n=2^15、r=8、p=1 约占 32 MiB
内存、单次几十毫秒，配合登录限速，离线爆破与在线撞库都不划算。

只存散列。格式 ``scrypt$N$r$p$salt$key``（salt、key 为 base64），参数跟着散列走，
日后提高参数不影响旧散列的校验。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_N, _R, _P = 2**15, 8, 1
_SALT_BYTES = 16
_KEY_BYTES = 32
# scrypt 占用约 128*N*r*p 字节；OpenSSL 默认上限 32 MiB 正好卡在边上，要给余量
_COST_BYTES = 128 * _N * _R * _P
_MAXMEM = _COST_BYTES * 2

MIN_LENGTH = 12
MAX_LENGTH = 256

_burn_hash: str | None = None


def validate_password(password: str) -> str:
    """返回不合规的原因；合规返回空串。

    长度是唯一的硬性规则（NIST SP 800-63B：长度优先；强制字符种类组合只会催生
    可预测的变体）。
    """
    if len(password) < MIN_LENGTH:
        return f"password must be at least {MIN_LENGTH} characters"
    if len(password) > MAX_LENGTH:
        return f"password must be at most {MAX_LENGTH} characters"
    return ""


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    key = _scrypt(password, salt, _N, _R, _P, _KEY_BYTES)
    return "$".join(
        (
            "scrypt",
            str(_N),
            str(_R),
            str(_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(key).decode(),
        )
    )


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比较。stored 格式不对或参数超出本实现的代价上限，一律判错，不抛异常。"""
    parsed = _parse(stored)
    if parsed is None:
        burn()
        return False
    n, r, p, salt, expected = parsed
    computed = _scrypt(password, salt, n, r, p, len(expected))
    return hmac.compare_digest(computed, expected)


def burn() -> None:
    """账号不存在时也照样算一次：「账号不存在」与「口令错误」耗时一致，不泄露账号是否存在。"""
    global _burn_hash
    if _burn_hash is None:
        _burn_hash = hash_password("skein-no-such-account")
    else:
        verify_password("skein-no-such-account", _burn_hash)


def _parse(stored: object) -> tuple[int, int, int, bytes, bytes] | None:
    if not isinstance(stored, str):
        return None
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        key = base64.b64decode(parts[5], validate=True)
    except ValueError:
        return None
    if n < 2 or n & (n - 1) or r < 1 or p < 1 or 128 * n * r * p > _COST_BYTES:
        return None
    if not salt or not key:
        return None
    return n, r, p, salt, key


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=length, maxmem=_MAXMEM
    )
