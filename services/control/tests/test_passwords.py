"""口令散列：参数、格式、校验，以及「账号不存在」与「口令错误」走同一条路。"""

from __future__ import annotations

import base64

import pytest

from skein import passwords

# 在 autouse 夹具把代价调低之前先记下真实参数
REAL_N, REAL_R, REAL_P = passwords._N, passwords._R, passwords._P
PASSWORD = "correct horse battery staple"


@pytest.fixture()
def real_cost(monkeypatch):
    monkeypatch.setattr(passwords, "_N", REAL_N)


def test_parameters_are_the_recommended_minimum_and_memory_hard():
    """OWASP 对 scrypt 的下限：N=2^15、r=8、p=1。内存占用 128·N·r·p = 32 MiB。"""
    assert (REAL_N, REAL_R, REAL_P) == (2**15, 8, 1)
    assert 128 * REAL_N * REAL_R * REAL_P == 32 * 1024 * 1024


def test_hash_format_and_roundtrip(real_cost):
    stored = passwords.hash_password(PASSWORD)
    scheme, n, r, p, salt, key = stored.split("$")
    assert (scheme, n, r, p) == ("scrypt", str(REAL_N), str(REAL_R), str(REAL_P))
    assert len(base64.b64decode(salt)) == 16
    assert len(base64.b64decode(key)) == 32
    assert PASSWORD not in stored
    assert passwords.verify_password(PASSWORD, stored) is True
    assert passwords.verify_password(PASSWORD + " ", stored) is False


def test_unicode_password_roundtrip(real_cost):
    stored = passwords.hash_password("口令-パスワード-🔐")
    assert passwords.verify_password("口令-パスワード-🔐", stored) is True
    assert passwords.verify_password("口令-パスワード", stored) is False


def test_every_hash_gets_a_fresh_salt():
    assert passwords.hash_password(PASSWORD) != passwords.hash_password(PASSWORD)


def test_parameters_travel_with_the_hash(monkeypatch):
    """旧散列记着自己的参数：日后提高代价，旧散列照样能验。"""
    monkeypatch.setattr(passwords, "_N", 1 << 11)
    older = passwords.hash_password(PASSWORD)
    assert older.split("$")[1] == "2048"
    monkeypatch.setattr(passwords, "_N", REAL_N)
    assert passwords.verify_password(PASSWORD, older) is True


@pytest.mark.parametrize(
    "stored",
    (
        "",
        "plaintext",
        "bcrypt$10$abc",
        "scrypt$0$8$1$AA$AA",
        "scrypt$3$8$1$AA$AA",  # 不是 2 的幂
        "scrypt$2097152$8$1$AA$AA",  # 超出本实现的上限：伪造的散列不能逼控制面烧内存
        "scrypt$32768$0$1$AA$AA",
        "scrypt$32768$8$1$!!!$AA",
        "scrypt$32768$8$1$AA",
        None,
        42,
    ),
)
def test_malformed_hashes_are_rejected_without_raising(stored):
    assert passwords.verify_password(PASSWORD, stored) is False


def test_password_rules():
    assert passwords.validate_password("short")
    assert passwords.validate_password("a" * (passwords.MIN_LENGTH - 1))
    assert passwords.validate_password("a" * passwords.MIN_LENGTH) == ""
    assert passwords.validate_password("a" * (passwords.MAX_LENGTH + 1))
    # 不强制字符种类组合：长短语比「大小写数字符号各一个」更强
    assert passwords.validate_password("all lowercase words here") == ""


def test_unknown_account_costs_the_same_as_a_wrong_password(monkeypatch):
    """账号不存在时也真的算一次 scrypt：响应耗时一致，不泄露账号是否存在。"""
    calls = []
    real = passwords.hashlib.scrypt

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(passwords.hashlib, "scrypt", counting)
    passwords.verify_password(PASSWORD, passwords.hash_password(PASSWORD))
    assert len(calls) == 2
    passwords.burn()
    assert len(calls) == 3
    passwords.verify_password(PASSWORD, "garbage")  # 格式不对也照样烧一次
    assert len(calls) == 4
