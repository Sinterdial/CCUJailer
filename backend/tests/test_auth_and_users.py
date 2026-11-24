"""
用户管理、本地账密、TOTP 二步验证与 ZITADEL OIDC PKCE 无密钥 SSO 单元测试模块。
"""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pyotp
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from cj.core.config import Settings
from cj.db.models import InviteCode
from main import create_app


def _make_test_rsa_jwk() -> tuple[Any, dict[str, Any]]:
    """
    生成测试用 RSA 私钥及对应的标准 JWK 公钥字典。

    @return: (RSA 私钥对象, JWKS 公钥集字典)
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "zitadel-test-key-1"
    public_jwk["use"] = "sig"
    public_jwk["alg"] = "RS256"
    return private_key, {"keys": [public_jwk]}


async def test_bootstrap_admin_totp_and_user_rbac(tmp_path: Path) -> None:
    """
    验证：
    1. 通过环境变量 INIT_ADMIN_* 启动自动创建管理员，且 /auth/setup 不可二次建号；
    2. 个人安全：绑定 TOTP 2FA 后账密登录进入 MFA 挑战、错误码拒签、正确码登录与解绑；
    3. 管理员新建/禁用/删除用户、普通用户越权返回 403、禁止删除自身或最后一名管理员。
    """
    data_dir = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'auth_rbac.db').as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        init_admin_username="root_admin",
        init_admin_password="RootPassword123!",
        frontend_dist_dir=tmp_path / "no_dist",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. 已由环境变量自动建号，web /auth/setup 应返回 409
            dup_setup = await client.post(
                "/api/v1/auth/setup",
                json={"username": "evil", "password": "Password123!"},
            )
            assert dup_setup.status_code == 409

            # 2. 登录管理员并绑定 TOTP 2FA
            login_res = await client.post(
                "/api/v1/auth/login",
                json={"username": "root_admin", "password": "RootPassword123!"},
            )
            assert login_res.status_code == 200
            admin_token = login_res.json()["access_token"]
            assert admin_token

            setup_otp = await client.post(
                "/api/v1/auth/otp/setup",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert setup_otp.status_code == 200
            otp_secret = setup_otp.json()["secret"]
            assert "<svg" in setup_otp.json()["qr_svg"]

            valid_code = pyotp.TOTP(otp_secret).now()
            enable_res = await client.post(
                "/api/v1/auth/otp/enable",
                json={"otp_code": valid_code},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert enable_res.status_code == 200
            assert enable_res.json()["totp_enabled"] is True

            # 3. 重新登录应返回 mfa_required=True 与短期挑战令牌
            mfa_step1 = await client.post(
                "/api/v1/auth/login",
                json={"username": "root_admin", "password": "RootPassword123!"},
            )
            assert mfa_step1.status_code == 200
            assert mfa_step1.json()["mfa_required"] is True
            mfa_token = mfa_step1.json()["mfa_token"]
            assert mfa_step1.json()["access_token"] is None

            # 挑战令牌不可直接当会话访问 /auth/me
            assert (
                await client.get(
                    "/api/v1/auth/me", headers={"Authorization": f"Bearer {mfa_token}"}
                )
            ).status_code == 401

            # 错误口令拒绝
            bad_otp = await client.post(
                "/api/v1/auth/login/otp",
                json={"mfa_token": mfa_token, "otp_code": "000000"},
            )
            assert bad_otp.status_code == 401

            # 正确 OTP 完成登录并解绑
            good_otp = await client.post(
                "/api/v1/auth/login/otp",
                json={"mfa_token": mfa_token, "otp_code": pyotp.TOTP(otp_secret).now()},
            )
            assert good_otp.status_code == 200
            admin_token2 = good_otp.json()["access_token"]
            assert admin_token2

            # 4. 创建普通用户并验证 RBAC 隔离与防自锁保护
            new_user_res = await client.post(
                "/api/v1/users",
                json={
                    "username": "pilot_user",
                    "password": "UserPass1234!",
                    "display_name": "普通玩家",
                    "email": "pilot@example.com",
                    "role": "user",
                    "is_active": True,
                },
                headers={"Authorization": f"Bearer {admin_token2}"},
            )
            assert new_user_res.status_code == 201
            pilot_id = new_user_res.json()["id"]

            # 禁止将唯一的管理员自身降级或删除
            demote_last = await client.put(
                f"/api/v1/users/{good_otp.json()['user']['id']}",
                json={"role": "user"},
                headers={"Authorization": f"Bearer {admin_token2}"},
            )
            assert demote_last.status_code == 403

            # 普通用户登录后可以查看 /store，但访问 /users 和 /settings 返回 403
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as user_cli:
                u_login = await user_cli.post(
                    "/api/v1/auth/login",
                    json={"username": "pilot_user", "password": "UserPass1234!"},
                )
                assert u_login.status_code == 200
                u_token = u_login.json()["access_token"]
                assert (
                    await user_cli.get(
                        "/api/v1/store", headers={"Authorization": f"Bearer {u_token}"}
                    )
                ).status_code == 200
                assert (
                    await user_cli.get(
                        "/api/v1/users", headers={"Authorization": f"Bearer {u_token}"}
                    )
                ).status_code == 403
                assert (
                    await user_cli.get(
                        "/api/v1/settings", headers={"Authorization": f"Bearer {u_token}"}
                    )
                ).status_code == 403

                # 管理员禁用该普通用户后，其既有 JWT 立即失效 (401)
                disable_res = await client.put(
                    f"/api/v1/users/{pilot_id}",
                    json={"is_active": False},
                    headers={"Authorization": f"Bearer {admin_token2}"},
                )
                assert disable_res.status_code == 200
                assert (
                    await user_cli.get(
                        "/api/v1/store", headers={"Authorization": f"Bearer {u_token}"}
                    )
                ).status_code == 401

            # 停用自己 OTP 并删除测试普通用户
            assert (
                await client.post(
                    "/api/v1/auth/otp/disable",
                    json={"current_password": "RootPassword123!"},
                    headers={"Authorization": f"Bearer {admin_token2}"},
                )
            ).status_code == 200
            assert (
                await client.delete(
                    f"/api/v1/users/{pilot_id}",
                    headers={"Authorization": f"Bearer {admin_token2}"},
                )
            ).status_code == 200


async def test_zitadel_oidc_pkce_secretless_sso_flow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """
    验证 ZITADEL OIDC Authorization Code + PKCE（S256 无 client_secret）完整认证流程：
    - 校验授权 URL 包含 code_challenge_method=S256；
    - 校验向 /oauth/v2/token 提交的表单含 code_verifier 且不包含 client_secret；
    - 校验基于 JWKS 公钥验签 id_token、拉取 userinfo 补全资料并完成自动建号登录；
    - 校验一次性 state 防重放。
    """
    private_key, jwks_payload = _make_test_rsa_jwk()
    captured_token_form: dict[str, str] = {}
    recorded_nonce = {"val": ""}

    issuer = "https://auth.example.com"
    client_id = "2900112233@ccujailer"

    def _oidc_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/oauth/v2/authorize",
                    "token_endpoint": f"{issuer}/oauth/v2/token",
                    "jwks_uri": f"{issuer}/oauth/v2/keys",
                    "userinfo_endpoint": f"{issuer}/oidc/v1/userinfo",
                },
            )
        if request.url.path == "/oauth/v2/keys":
            return httpx.Response(200, json=jwks_payload)
        if request.url.path == "/oauth/v2/token":
            parsed = parse_qs(request.content.decode("utf-8"))
            for k, vals in parsed.items():
                captured_token_form[k] = vals[0]
            assert "client_secret" not in captured_token_form
            assert captured_token_form["grant_type"] == "authorization_code"
            assert captured_token_form["client_id"] == client_id
            assert captured_token_form["code"] == "zitadel-auth-code-999"
            assert len(captured_token_form["code_verifier"]) >= 43

            now_ts = int(time.time())
            id_tok = jwt.encode(
                {
                    "iss": issuer,
                    "sub": "zitadel-sub-10086",
                    "aud": client_id,
                    "nonce": recorded_nonce["val"],
                    "iat": now_ts,
                    "exp": now_ts + 300,
                },
                private_key,
                algorithm="RS256",
                headers={"kid": "zitadel-test-key-1"},
            )
            return httpx.Response(
                200,
                json={"id_token": id_tok, "access_token": "zitadel-access-token"},
            )
        if request.url.path == "/oidc/v1/userinfo":
            assert request.headers.get("Authorization") == "Bearer zitadel-access-token"
            return httpx.Response(
                200,
                json={
                    "sub": "zitadel-sub-10086",
                    "preferred_username": "star_citizen_fan",
                    "name": "Star Citizen Fan",
                    "email": "fan@example.com",
                },
            )
        return httpx.Response(404)

    data_dir = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'sso.db').as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        oidc_enabled=True,
        oidc_provider_name="ZITADEL",
        oidc_issuer_url=issuer,
        oidc_client_id=client_id,
        oidc_auto_register=True,
        oidc_default_role="user",
        frontend_dist_dir=tmp_path / "no_dist",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        monkeypatch.setattr(
            app.state.container.net,
            "_proxy_client",
            httpx.AsyncClient(transport=httpx.MockTransport(_oidc_handler)),
        )

        # 预先建立一名本地管理员以便本次 SSO 注册用户按默认 user 角色入库
        await app.state.container.auth_service.setup_initial_admin(
            username="local_admin", password="LocalAdminPassword123"
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            redirect_uri = "https://ccu.example.com/auth/callback"
            auth_url_resp = await client.get(
                "/api/v1/auth/sso/authorize",
                params={"redirect_uri": redirect_uri},
            )
            assert auth_url_resp.status_code == 200
            auth_url = auth_url_resp.json()["authorize_url"]
            qs = parse_qs(urlsplit(auth_url).query)
            assert qs["code_challenge_method"] == ["S256"]
            assert qs["client_id"] == [client_id]
            state_val = qs["state"][0]
            recorded_nonce["val"] = qs["nonce"][0]

            # 回调换票
            cb_resp = await client.post(
                "/api/v1/auth/sso/callback",
                json={
                    "code": "zitadel-auth-code-999",
                    "state": state_val,
                    "redirect_uri": redirect_uri,
                },
            )
            assert cb_resp.status_code == 200
            body = cb_resp.json()
            assert body["user"]["username"] == "star_citizen_fan"
            assert body["user"]["email"] == "fan@example.com"
            assert body["user"]["oidc_bound"] is True
            assert body["user"]["role"] == "user"

            # 相同 state 重放必须失败
            replay = await client.post(
                "/api/v1/auth/sso/callback",
                json={
                    "code": "zitadel-auth-code-999",
                    "state": state_val,
                    "redirect_uri": redirect_uri,
                },
            )
            assert replay.status_code == 400


async def test_registration_modes_and_invite_codes(tmp_path: Path) -> None:
    """
    验证三种用户注册模式与邀请码全流程：
    1. 默认 closed 模式下拒绝一切自助注册（403）；
    2. invite_only 模式下缺省/不存在/已用尽/已停用/已过期邀请码均拒绝（400），
       有效码可成功注册并递增已用次数；
    3. open 模式下免邀请码即可注册普通 user 账号；
    4. 切回 closed 后再次拒绝注册。
    """
    data_dir = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'reg_modes.db').as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        init_admin_username="root_admin",
        init_admin_password="RootPassword123!",
        frontend_dist_dir=tmp_path / "no_dist",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            status_res = await client.get("/api/v1/auth/status")
            assert status_res.status_code == 200
            assert status_res.json()["registration_mode"] == "closed"

            # 1. 默认 closed 模式拒绝注册
            closed_reg = await client.post(
                "/api/v1/auth/register",
                json={"username": "guest_a", "password": "GuestPassword123"},
            )
            assert closed_reg.status_code == 403

            # 登录管理员切换为 invite_only
            login_res = await client.post(
                "/api/v1/auth/login",
                json={"username": "root_admin", "password": "RootPassword123!"},
            )
            admin_token = login_res.json()["access_token"]
            admin_hdrs = {"Authorization": f"Bearer {admin_token}"}

            mode_res = await client.put(
                "/api/v1/settings",
                json={"registration_mode": "invite_only"},
                headers=admin_hdrs,
            )
            assert mode_res.status_code == 200
            assert mode_res.json()["registration_mode"] == "invite_only"

            # 2. 邀请制校验
            missing_inv = await client.post(
                "/api/v1/auth/register",
                json={"username": "guest_b", "password": "GuestPassword123"},
            )
            assert missing_inv.status_code == 400

            bad_inv = await client.post(
                "/api/v1/auth/register",
                json={
                    "username": "guest_b",
                    "password": "GuestPassword123",
                    "invite_code": "INVALID-CODE",
                },
            )
            assert bad_inv.status_code == 400

            inv_one = await client.post(
                "/api/v1/users/invites",
                json={"role": "user", "max_uses": 1, "expires_in_hours": 24, "note": "Single use"},
                headers=admin_hdrs,
            )
            assert inv_one.status_code == 201
            code_one = inv_one.json()["code"]

            # 生成第二个邀请码并立刻停用，验证停用码无法使用
            inv_disabled = await client.post(
                "/api/v1/users/invites",
                json={"role": "user", "max_uses": 5, "expires_in_hours": None, "note": "Disabled"},
                headers=admin_hdrs,
            )
            disabled_id = inv_disabled.json()["id"]
            disabled_code = inv_disabled.json()["code"]
            await client.put(
                f"/api/v1/users/invites/{disabled_id}",
                json={"is_active": False},
                headers=admin_hdrs,
            )
            disabled_reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "username": "guest_dis",
                    "password": "GuestPassword123",
                    "invite_code": disabled_code,
                },
            )
            assert disabled_reg.status_code == 400

            # 使用有效限次 1 次的邀请码注册成功
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as guest_cli:
                reg_ok = await guest_cli.post(
                    "/api/v1/auth/register",
                    json={
                        "username": "invite_pilot",
                        "password": "InvitePassword123",
                        "display_name": "受邀飞行员",
                        "email": "invite@example.com",
                        "invite_code": code_one,
                    },
                )
                assert reg_ok.status_code == 201
                assert reg_ok.json()["user"]["username"] == "invite_pilot"
                assert reg_ok.json()["user"]["role"] == "user"
                assert reg_ok.json()["access_token"]

            # 复用已耗尽邀请码必须被拒绝
            reuse_res = await client.post(
                "/api/v1/auth/register",
                json={
                    "username": "invite_pilot_2",
                    "password": "InvitePassword123",
                    "invite_code": code_one,
                },
            )
            assert reuse_res.status_code == 400

            # 已过期邀请码必须被拒绝
            inv_exp = await client.post(
                "/api/v1/users/invites",
                json={"role": "user", "max_uses": 3, "expires_in_hours": 1, "note": "Expired"},
                headers=admin_hdrs,
            )
            exp_id = inv_exp.json()["id"]
            exp_code = inv_exp.json()["code"]

            async with app.state.container.session_factory() as db_sess:
                async with db_sess.begin():
                    row = (
                        await db_sess.execute(select(InviteCode).where(InviteCode.id == exp_id))
                    ).scalar_one()
                    row.expires_at = datetime.now(UTC) - timedelta(minutes=5)

            expired_reg = await client.post(
                "/api/v1/auth/register",
                json={
                    "username": "guest_exp",
                    "password": "GuestPassword123",
                    "invite_code": exp_code,
                },
            )
            assert expired_reg.status_code == 400

            # 核查邀请码列表中的 used_count 已更新为 1
            invites_list = await client.get("/api/v1/users/invites", headers=admin_hdrs)
            inv_id_one = inv_one.json()["id"]
            matched_inv = next(i for i in invites_list.json()["items"] if i["id"] == inv_id_one)
            assert matched_inv["used_count"] == 1

            # 3. 开放注册模式免邀请码注册
            await client.put(
                "/api/v1/settings",
                json={"registration_mode": "open"},
                headers=admin_hdrs,
            )
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as open_cli:
                open_reg = await open_cli.post(
                    "/api/v1/auth/register",
                    json={
                        "username": "open_pilot",
                        "password": "OpenPassword123",
                    },
                )
                assert open_reg.status_code == 201
                assert open_reg.json()["user"]["role"] == "user"

            # 4. 切回 closed 后阻止新注册
            await client.put(
                "/api/v1/settings",
                json={"registration_mode": "closed"},
                headers=admin_hdrs,
            )
            re_closed = await client.post(
                "/api/v1/auth/register",
                json={"username": "late_pilot", "password": "LatePassword123"},
            )
            assert re_closed.status_code == 403
