"""
用户账号与 OIDC 临时状态数据访问层（Repository）。

严格接收调用方传入的 AsyncSession，禁止在内部自行 commit 或 rollback。
"""

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cj.db.models import InviteCode, OidcState, User, UserPasskey, WebAuthnChallenge


class UserRepository:
    """
    用户与 OIDC PKCE 状态 ORM 仓库。
    """

    async def count_users(self, session: AsyncSession) -> int:
        """
        统计系统当前已注册账号总数。

        @param session: 异步数据库会话
        @return: 用户总数
        """
        stmt = select(func.count()).select_from(User)
        return int((await session.execute(stmt)).scalar_one())

    async def count_active_admins(self, session: AsyncSession) -> int:
        """
        统计当前处于启用状态的管理员数量，用于防止系统失去最后一个管理员。

        @param session: 异步数据库会话
        @return: 活跃管理员总数
        """
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True))
        )
        return int((await session.execute(stmt)).scalar_one())

    async def get_by_id(self, session: AsyncSession, user_id: int) -> User | None:
        """
        根据主键 ID 查询用户实体。

        @param session: 异步数据库会话
        @param user_id: 用户主键 ID
        @return: User 实例或 None
        """
        return await session.get(User, user_id)

    async def get_by_username(self, session: AsyncSession, username: str) -> User | None:
        """
        按归一化小写用户名精准查询用户。

        @param session: 异步数据库会话
        @param username: 登录用户名
        @return: User 实例或 None
        """
        stmt = select(User).where(func.lower(User.username) == username.strip().lower())
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_oidc(
        self,
        session: AsyncSession,
        issuer: str,
        subject: str,
    ) -> User | None:
        """
        根据 OIDC Issuer 与 Subject 唯一标识查询已绑定账号。

        @param session: 异步数据库会话
        @param issuer: OIDC Issuer URL
        @param subject: OIDC sub 唯一用户标识
        @return: User 实例或 None
        """
        stmt = select(User).where(User.oidc_issuer == issuer, User.oidc_sub == subject)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, session: AsyncSession) -> list[User]:
        """
        查询系统全量用户列表，按 ID 升序排列。

        @param session: 异步数据库会话
        @return: User 实体列表
        """
        stmt = select(User).order_by(User.id.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def add_user(self, session: AsyncSession, user: User) -> User:
        """
        将新用户实体写入当前会话并 flush 生成自增 ID。

        @param session: 异步数据库会话
        @param user: 待写入 User 实体
        @return: 已分配主键的 User 实体
        """
        session.add(user)
        await session.flush()
        return user

    async def delete_user(self, session: AsyncSession, user: User) -> None:
        """
        从会话中删除指定用户实体并 flush。

        @param session: 异步数据库会话
        @param user: 待删除 User 实体
        """
        await session.delete(user)
        await session.flush()

    async def add_oidc_state(self, session: AsyncSession, state_row: OidcState) -> None:
        """
        保存一条 OIDC PKCE 短期临时状态记录。

        @param session: 异步数据库会话
        @param state_row: OidcState 实体
        """
        session.add(state_row)
        await session.flush()

    async def pop_oidc_state(self, session: AsyncSession, state: str) -> OidcState | None:
        """
        按 state 主键提取并立即删除对应的 OIDC 临时状态（确保单次消费防重放）。

        @param session: 异步数据库会话
        @param state: 回调传入的 state 字符串
        @return: 命中的 OidcState 对象或 None
        """
        row = await session.get(OidcState, state)
        if row is not None:
            await session.delete(row)
            await session.flush()
        return row

    async def delete_expired_oidc_states(self, session: AsyncSession, now: datetime) -> int:
        """
        清理所有已过期的 OIDC 临时状态记录。

        @param session: 异步数据库会话
        @param now: 当前 UTC 时间
        @return: 删除的记录数
        """
        stmt = delete(OidcState).where(OidcState.expires_at < now)
        result = await session.execute(stmt)
        return int(result.rowcount or 0)

    async def list_invite_codes(self, session: AsyncSession) -> list[InviteCode]:
        """
        查询全部邀请注册码记录，按创建时间倒序排列。

        @param session: 异步数据库会话
        @return: InviteCode 实体列表
        """
        stmt = select(InviteCode).order_by(InviteCode.id.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_invite_by_id(self, session: AsyncSession, invite_id: int) -> InviteCode | None:
        """
        根据主键 ID 查询邀请码记录。

        @param session: 异步数据库会话
        @param invite_id: 邀请码 ID
        @return: InviteCode 实例或 None
        """
        return await session.get(InviteCode, invite_id)

    async def get_invite_by_code(self, session: AsyncSession, code: str) -> InviteCode | None:
        """
        按大写归一化字符串精准查询邀请码记录。

        @param session: 异步数据库会话
        @param code: 用户输入的邀请码字符串
        @return: InviteCode 实例或 None
        """
        normalized = code.strip().upper()
        if not normalized:
            return None
        stmt = select(InviteCode).where(func.upper(InviteCode.code) == normalized)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_invite_code(self, session: AsyncSession, invite: InviteCode) -> InviteCode:
        """
        持久化新建邀请码记录并 flush 分配主键。

        @param session: 异步数据库会话
        @param invite: 待写入的 InviteCode 实体
        @return: 已入库的 InviteCode 实体
        """
        session.add(invite)
        await session.flush()
        return invite

    async def delete_invite_code(self, session: AsyncSession, invite: InviteCode) -> None:
        """
        删除指定邀请码记录并 flush。

        @param session: 异步数据库会话
        @param invite: 待删除 InviteCode 实体
        """
        await session.delete(invite)
        await session.flush()

    async def list_passkeys_by_user(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> list[UserPasskey]:
        """
        查询指定用户已绑定的全部 Passkey 通行密钥列表。

        @param session: 异步数据库会话
        @param user_id: 用户主键 ID
        @return: 按创建时间升序排列的 UserPasskey 列表
        """
        stmt = (
            select(UserPasskey)
            .where(UserPasskey.user_id == user_id)
            .order_by(UserPasskey.id.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def count_passkeys_by_users(
        self,
        session: AsyncSession,
        user_ids: list[int],
    ) -> dict[int, int]:
        """
        批量统计多位用户的已绑定 Passkey 数量。

        @param session: 异步数据库会话
        @param user_ids: 用户主键 ID 列表
        @return: {user_id: passkey_count} 映射字典
        """
        if not user_ids:
            return {}
        stmt = (
            select(UserPasskey.user_id, func.count(UserPasskey.id))
            .where(UserPasskey.user_id.in_(user_ids))
            .group_by(UserPasskey.user_id)
        )
        rows = (await session.execute(stmt)).all()
        return {int(uid): int(cnt) for uid, cnt in rows}

    async def get_passkey_by_id(
        self,
        session: AsyncSession,
        passkey_id: int,
    ) -> UserPasskey | None:
        """
        根据主键 ID 获取单个 Passkey 记录。

        @param session: 异步数据库会话
        @param passkey_id: Passkey 主键 ID
        @return: UserPasskey 实例或 None
        """
        return await session.get(UserPasskey, passkey_id)

    async def get_passkey_by_credential_id(
        self,
        session: AsyncSession,
        credential_id: str,
    ) -> UserPasskey | None:
        """
        根据 WebAuthn Credential ID 查询 Passkey 凭据记录。

        @param session: 异步数据库会话
        @param credential_id: Base64URL 编码的 Credential ID
        @return: UserPasskey 实例或 None
        """
        stmt = select(UserPasskey).where(UserPasskey.credential_id == credential_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_passkey(self, session: AsyncSession, passkey: UserPasskey) -> UserPasskey:
        """
        持久化新的 Passkey 通行密钥记录并 flush。

        @param session: 异步数据库会话
        @param passkey: 待写入的 UserPasskey 实体
        @return: 已分配主键的 UserPasskey 实体
        """
        session.add(passkey)
        await session.flush()
        return passkey

    async def delete_passkey(self, session: AsyncSession, passkey: UserPasskey) -> None:
        """
        删除指定 Passkey 凭据记录并 flush。

        @param session: 异步数据库会话
        @param passkey: 待删除 UserPasskey 实体
        """
        await session.delete(passkey)
        await session.flush()

    async def add_webauthn_challenge(
        self,
        session: AsyncSession,
        challenge_row: WebAuthnChallenge,
    ) -> None:
        """
        保存一条短期有效的 WebAuthn 注册或认证挑战记录。

        @param session: 异步数据库会话
        @param challenge_row: WebAuthnChallenge 实体
        """
        session.add(challenge_row)
        await session.flush()

    async def pop_webauthn_challenge(
        self,
        session: AsyncSession,
        challenge_id: str,
    ) -> WebAuthnChallenge | None:
        """
        按挑战 ID 提取并立即删除对应的 WebAuthn 临时挑战（防重放单次消费）。

        @param session: 异步数据库会话
        @param challenge_id: 挑战唯一 ID
        @return: WebAuthnChallenge 实例或 None
        """
        row = await session.get(WebAuthnChallenge, challenge_id)
        if row is not None:
            await session.delete(row)
            await session.flush()
        return row

    async def delete_expired_webauthn_challenges(
        self,
        session: AsyncSession,
        now: datetime,
    ) -> int:
        """
        清理所有已过期的 WebAuthn 挑战记录。

        @param session: 异步数据库会话
        @param now: 当前 UTC 时间
        @return: 删除的记录数
        """
        stmt = delete(WebAuthnChallenge).where(WebAuthnChallenge.expires_at < now)
        result = await session.execute(stmt)
        return int(result.rowcount or 0)
