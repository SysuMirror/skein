"""账号目录：一个人一条 users 记录，角色存在雁阵这里；登录方式只是挂在人身上的身份。

provider 只回答「这是谁」。第一次见到一个身份时怎么办，由这里决定：

- 上游说他是管理员（``super_admin`` → owner，``admin`` → admin）：自动建号。这样升级
  不会把任何人锁在门外——已经在上游当管理员的人，升级后立刻还是管理员。
- 其他人默认不建号（与历史行为一致：非管理员进不了控制台），要由 owner 预先建号，
  或者部署方显式打开 ``auto_role``。
- 之后角色记在雁阵里，由 owner 用命令行改。

角色怎么生效，见 :func:`effective_role`：member 只由雁阵授予；admin 和 owner 对
「管理员标记是权威的」provider（``admin_claims=True``，如 UniSSO）还要求上游此刻也这么说。
每个请求都现算、不落库，所以上游撤权立刻生效、上游恢复也立刻恢复，不会因为一次
抖动把所有管理员永久降级。

绝不按邮箱合并账号：邮箱只是 provider 报上来的属性，雁阵没有验证过。要把另一种
登录方式挂到已有的人身上，只能显式 :meth:`Directory.link`。

所有写操作都是「先校验、后落库」的单条语句：连接是自动提交模式，多线程共用，
这里不开显式事务。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, replace

from skein import access, db, passwords
from skein.access import ADMIN, MEMBER, OWNER, ROLES, SESSION
from skein.auth import AuthRejected, Principal
from skein.sessions import SessionStore

TOKEN_PROVIDER = "token"
TOKEN_HOLDER = "token-holder"
LOCAL_PROVIDER = "local"
# 「认得出是谁，但没有进控制台的资格」
NO_ACCESS = "none"

# 身份行的 attrs / last_login_at 刷新间隔：只为排障留个最近一次的快照
_PROFILE_SYNC_S = 3600.0

_USER_FIELDS = (
    "sub, email, name, picture, role, disabled, password_hash, created_at, updated_at"
)


def token_identity() -> Principal:
    """令牌模式 provider 眼里的身份：只有「这是那个持有主令牌的人」这一件事。"""
    return Principal(sub=TOKEN_HOLDER, name="令牌持有者", internal=True, super_admin=True)


def token_principal() -> Principal:
    """零配置模式下持有主令牌的人：拥有部署的全部权限（环境文件本来就在他手里）。"""
    return replace(token_identity(), kind=SESSION, role=OWNER, provider=TOKEN_PROVIDER)


def role_from_claims(identity: Principal) -> str:
    """把上游的管理员标记映射成雁阵角色。没有标记是 NO_ACCESS——不是 member：
    member 得由雁阵自己授予，上游的「不是管理员」不等于「给他只读权限」。"""
    if identity.super_admin:
        return OWNER
    if identity.admin:
        return ADMIN
    return NO_ACCESS


def effective_role(stored: str, upstream: str) -> str:
    """雁阵记的角色 + 上游此刻的管理员资格 → 现在按哪个角色放行。

    - member 只由雁阵授予，与上游无关。
    - admin / owner 还要求上游也给了不低于它的资格；上游收回多少，这里就低到多少。
    """
    if stored == MEMBER:
        return MEMBER
    return stored if access.rank(upstream) >= access.rank(stored) else upstream


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


@dataclass(frozen=True)
class User:
    sub: str
    email: str
    name: str
    picture: str
    role: str
    disabled: bool
    has_password: bool
    created_at: float
    updated_at: float

    def as_dict(self) -> dict:
        return {
            "sub": self.sub,
            "email": self.email,
            "name": self.name,
            "picture": self.picture,
            "role": self.role,
            "disabled": self.disabled,
            "has_password": self.has_password,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _user(row: sqlite3.Row) -> User:
    return User(
        sub=row["sub"],
        email=row["email"],
        name=row["name"],
        picture=row["picture"],
        role=row["role"],
        disabled=bool(row["disabled"]),
        has_password=bool(row["password_hash"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Directory:
    def __init__(
        self,
        conn: sqlite3.Connection,
        auto_role: str = "",
        sessions: SessionStore | None = None,
    ) -> None:
        """``auto_role``：第一次见到的、上游没说他是管理员的身份，要不要自动建号、给
        什么角色。空串 = 不建（默认）；目前只接受 ``member``。"""
        if auto_role and auto_role != MEMBER:
            raise ValueError("auto_role must be empty or 'member'")
        self._conn = conn
        self._auto_role = auto_role
        # 改口令 / 停用账号时要作废会话；没有会话库（纯目录用法）就跳过
        self._sessions = sessions

    # ---------------- 查 ----------------

    def get(self, sub: str) -> User | None:
        row = self._conn.execute(
            f"SELECT {_USER_FIELDS} FROM users WHERE sub = ?", (sub,)
        ).fetchone()
        return _user(row) if row else None

    def find(self, ref: str) -> User | None:
        """命令行用的定位：先按 sub 精确找，再按本地账号的邮箱找（不区分大小写）。

        外部账号的邮箱是上游报的、可能重复，不能拿来定位——那些账号请用 sub。
        """
        user = self.get(ref)
        if user is not None:
            return user
        return self.local_account(ref)

    def list(self) -> list[User]:
        rows = self._conn.execute(
            f"SELECT {_USER_FIELDS} FROM users ORDER BY created_at, sub"
        ).fetchall()
        return [_user(row) for row in rows]

    def identities(self, sub: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT provider, subject, created_at, last_login_at FROM identities"
            " WHERE user_sub = ? ORDER BY provider, subject",
            (sub,),
        ).fetchall()
        return [dict(row) for row in rows]

    def local_account(self, email: str) -> User | None:
        """本地账号：有口令，且身份 (local, 邮箱) 挂在这个人身上。"""
        row = self._conn.execute(
            "SELECT i.user_sub FROM identities i JOIN users u ON u.sub = i.user_sub"
            " WHERE i.provider = ? AND i.subject = ? AND u.password_hash <> ''",
            (LOCAL_PROVIDER, normalize_email(email)),
        ).fetchone()
        return self.get(row["user_sub"]) if row else None

    def password_hash(self, sub: str) -> str:
        row = self._conn.execute(
            "SELECT password_hash FROM users WHERE sub = ?", (sub,)
        ).fetchone()
        return str(row["password_hash"]) if row else ""

    def owner_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = ? AND disabled = 0", (OWNER,)
        ).fetchone()
        return int(row[0])

    # ---------------- 登录时：身份 → 人 → 角色 ----------------

    def principal_for(
        self,
        user: User,
        identity: Principal | None = None,
        provider: str = "",
        role: str | None = None,
    ) -> Principal:
        """账号 → 可授权的主体。管理员标记由角色反推，只用于给聊天上游签身份头。"""
        base = identity or Principal(sub=user.sub, internal=True)
        role = user.role if role is None else role
        return Principal(
            sub=user.sub,
            name=user.name or base.name,
            email=user.email or base.email,
            internal=base.internal,
            picture=user.picture or base.picture,
            admin=access.at_least(role, ADMIN),
            super_admin=role == OWNER,
            kind=SESSION,
            role=role,
            provider=provider,
        )

    def admit(
        self, identity: Principal, provider: str, *, admin_claims: bool = False
    ) -> Principal:
        """provider 认出的身份 → 雁阵里的人。没账号、没资格、账号停用都拒绝。"""
        if provider == TOKEN_PROVIDER:
            return token_principal()
        user = self.resolve(provider, identity)
        if user.disabled:
            raise AuthRejected("account_disabled", 403)
        role = user.role
        if admin_claims:
            role = effective_role(user.role, role_from_claims(identity))
        # 任何角色（含 member）都能进控制台，能做多少由权限矩阵决定；只有「没资格」才拒
        if access.rank(role) < access.rank(MEMBER):
            raise AuthRejected("not_admin", 403)
        return self.principal_for(user, identity, provider, role)

    def resolve(self, provider: str, identity: Principal) -> User:
        """身份 → 这个人。第一次见到按上面的规则建号；之后同步资料。"""
        now = time.time()
        row = self._conn.execute(
            "SELECT user_sub FROM identities WHERE provider = ? AND subject = ?",
            (provider, identity.sub),
        ).fetchone()
        if row is None:
            return self._provision(provider, identity)
        user = self.get(row["user_sub"])
        if user is None:
            raise AuthRejected("not_admin", 403)
        user = self._sync_profile(user, identity)
        self._conn.execute(
            "UPDATE identities SET attrs = ?, last_login_at = ?"
            " WHERE provider = ? AND subject = ? AND (last_login_at IS NULL OR last_login_at < ?)",
            (
                json.dumps(identity.as_dict(), ensure_ascii=False),
                now,
                provider,
                identity.sub,
                now - _PROFILE_SYNC_S,
            ),
        )
        return user

    def _provision(self, provider: str, identity: Principal) -> User:
        mapped = role_from_claims(identity)
        if access.rank(mapped) < access.rank(ADMIN) and not self._auto_role:
            raise AuthRejected("not_admin", 403)
        role = mapped if access.rank(mapped) >= access.rank(ADMIN) else self._auto_role
        user = self._insert_user(
            email=identity.email,
            name=identity.name,
            picture=identity.picture,
            role=role,
            password="",
        )
        try:
            self._link(user.sub, provider, identity.sub)
        except ValueError:
            # 同一个身份的两个请求同时首次到达：另一个已经建好号了，用它
            return self.resolve(provider, identity)
        db.record_audit(
            self._conn,
            "skein",
            "user.provisioned",
            {"sub": user.sub, "provider": provider, "subject": identity.sub, "role": role},
        )
        return user

    def _sync_profile(self, user: User, identity: Principal) -> User:
        """名字、邮箱、头像以 provider 报上来的为准；没报的保留原值。"""
        changes = {
            field: value
            for field, value in (
                ("name", identity.name),
                ("email", identity.email),
                ("picture", identity.picture),
            )
            if value and value != getattr(user, field)
        }
        if not changes:
            return user
        self._conn.execute(
            "UPDATE users SET "
            + ", ".join(f"{field} = ?" for field in changes)
            + ", updated_at = ? WHERE sub = ?",
            (*changes.values(), time.time(), user.sub),
        )
        return replace(user, **changes)

    # ---------------- 管理：owner 用命令行做 ----------------

    def create(
        self,
        *,
        email: str = "",
        name: str = "",
        role: str = MEMBER,
        password: str = "",
        provider: str = "",
        subject: str = "",
        actor: str = "skein",
    ) -> User:
        """建一个人。给了 ``password`` 就同时能本地登录（身份 = 邮箱）；给了
        ``provider`` + ``subject`` 就是预先给一个外部身份留位。"""
        self._check_role(role)
        email = normalize_email(email)
        local = bool(password)
        if local:
            problem = passwords.validate_password(password)
            if problem:
                raise ValueError(problem)
            if not email:
                raise ValueError("a local account needs an email address")
            if self.local_account(email) is not None:
                raise ValueError(f"a local account for {email} already exists")
        if provider or subject:
            if not (provider and subject):
                raise ValueError("provider and subject go together")
            if self._identity_exists(provider, subject):
                raise ValueError(f"identity {provider}:{subject} is already linked")
        user = self._insert_user(
            email=email, name=name, role=role, password=password if local else ""
        )
        if local:
            self._link(user.sub, LOCAL_PROVIDER, email)
        if provider and subject:
            self._link(user.sub, provider, subject)
        db.record_audit(
            self._conn,
            actor,
            "user.create",
            {"sub": user.sub, "email": email, "role": role, "local": local, "provider": provider},
        )
        return user

    def set_role(self, ref: str, role: str, actor: str = "skein") -> User:
        self._check_role(role)
        user = self._need(ref)
        if user.role == role:
            return user
        self._guard_last_owner(user, demoting_to=role)
        self._set_role_column(user.sub, role)
        db.record_audit(
            self._conn, actor, "user.role", {"sub": user.sub, "from": user.role, "to": role}
        )
        return replace(user, role=role)

    def set_password(self, ref: str, password: str, actor: str = "skein") -> User:
        """管理员重置口令。必须是一个本地账号；重置后这个人已有的会话全部作废。"""
        problem = passwords.validate_password(password)
        if problem:
            raise ValueError(problem)
        user = self._need(ref)
        if not user.has_password:
            raise ValueError(f"{ref} is not a local account")
        self._conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE sub = ?",
            (passwords.hash_password(password), time.time(), user.sub),
        )
        self._revoke_sessions(user.sub)
        db.record_audit(self._conn, actor, "user.password", {"sub": user.sub})
        return user

    def set_disabled(self, ref: str, disabled: bool, actor: str = "skein") -> User:
        user = self._need(ref)
        if user.disabled == disabled:
            return user
        if disabled:
            self._guard_last_owner(user)
        self._conn.execute(
            "UPDATE users SET disabled = ?, updated_at = ? WHERE sub = ?",
            (1 if disabled else 0, time.time(), user.sub),
        )
        if disabled:
            self._revoke_sessions(user.sub)
        db.record_audit(
            self._conn,
            actor,
            "user.disabled" if disabled else "user.enabled",
            {"sub": user.sub},
        )
        return replace(user, disabled=disabled)

    def link(self, ref: str, provider: str, subject: str, actor: str = "skein") -> None:
        """把一个外部身份显式挂到已有的人身上（不按邮箱自动合并）。"""
        user = self._need(ref)
        if not provider or not subject:
            raise ValueError("provider and subject are required")
        self._link(user.sub, provider, subject)
        db.record_audit(
            self._conn,
            actor,
            "user.link",
            {"sub": user.sub, "provider": provider, "subject": subject},
        )

    # ---------------- 内部 ----------------

    @staticmethod
    def _check_role(role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")

    def _need(self, ref: str) -> User:
        user = self.find(ref)
        if user is None:
            hint = (
                f"no local account for {ref}; accounts without a password"
                " are addressed by their sub (usr_…)"
                if "@" in ref
                else f"no such user: {ref}"
            )
            raise LookupError(hint)
        return user

    def _guard_last_owner(self, user: User, demoting_to: str | None = None) -> None:
        """不能降级或停用最后一个 owner——否则再也没人能做 owner 的事。"""
        leaving = user.role == OWNER and (demoting_to is None or demoting_to != OWNER)
        if leaving and not user.disabled and self.owner_count() <= 1:
            raise ValueError("this is the last owner; promote someone else first")

    def _insert_user(self, *, email: str, role: str, password: str, name: str = "", picture: str = "") -> User:
        now = time.time()
        sub = "usr_" + secrets.token_hex(6)
        self._conn.execute(
            "INSERT INTO users"
            " (sub, email, name, picture, role, password_hash, disabled, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (
                sub,
                email,
                (name or "").strip(),
                picture,
                role,
                passwords.hash_password(password) if password else "",
                now,
                now,
            ),
        )
        found = self.get(sub)
        assert found is not None
        return found

    def _set_role_column(self, sub: str, role: str) -> None:
        self._conn.execute(
            "UPDATE users SET role = ?, updated_at = ? WHERE sub = ?", (role, time.time(), sub)
        )

    def _identity_exists(self, provider: str, subject: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM identities WHERE provider = ? AND subject = ?", (provider, subject)
            ).fetchone()
            is not None
        )

    def _revoke_sessions(self, sub: str) -> None:
        if self._sessions is not None:
            self._sessions.revoke_user(sub)

    def _link(self, sub: str, provider: str, subject: str) -> None:
        if provider == TOKEN_PROVIDER:
            raise ValueError("the token holder is not an account and cannot be linked")
        try:
            self._conn.execute(
                "INSERT INTO identities"
                " (provider, subject, user_sub, attrs, created_at, last_login_at)"
                " VALUES (?, ?, ?, '{}', ?, NULL)",
                (provider, subject, sub, time.time()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"identity {provider}:{subject} is already linked") from exc
