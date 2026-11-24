"""
安全、敏感信息脱敏、密码哈希与对称加密工具模块。

负责 URL 凭据脱敏、Webhook 请求头掩码处理、日志文本密钥清理、管理令牌常量时间校验、
Argon2id 密码哈希以及基于主密钥派生的 TOTP 密钥加密与主密钥持久化管理。
"""

import base64
import hashlib
import hmac
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

MASK_PREFIX = "******"
SENSITIVE_HEADER_KEYWORDS = (
    "authorization",
    "token",
    "api-key",
    "apikey",
    "secret",
    "password",
    "cookie",
    "signature",
)

_URL_CREDENTIALS_PATTERN = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^:/@\s]+:)([^@\s]+)(@)")
_BEARER_PATTERN = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-+/=]{6,})", re.IGNORECASE)
_QUERY_SECRET_PATTERN = re.compile(
    r"([?&](?:token|key|secret|password|access_token|id_token|code|code_verifier|mfa_token)=)([^&\s]+)",
    re.IGNORECASE,
)

_PASSWORD_HASHER = PasswordHasher()
# 预计算一条合法 Argon2id 虚拟哈希，用于在账号不存在时执行恒定耗时比对防用户名枚举
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash("ccujailer-constant-time-dummy-password")


def mask_secret_value(value: str) -> str:
    """
    对单个敏感字符串执行掩码脱敏，最多保留末尾 4 个字符。

    @param value: 原始敏感字符串
    @return: 脱敏后的掩码文本
    """
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 6:
        return MASK_PREFIX
    return f"{MASK_PREFIX}{cleaned[-4:]}"


def is_masked_value(value: str) -> bool:
    """
    判断字符串是否为前端回传的脱敏占位符，用于修改配置时保留原敏感值。

    @param value: 待检测的字符串
    @return: 若为脱敏占位符返回 True，否则返回 False
    """
    return value.strip().startswith(MASK_PREFIX)


def is_sensitive_header(header_name: str) -> bool:
    """
    判断 HTTP Header 名称是否包含敏感凭据关键词。

    @param header_name: Header 键名
    @return: 属于敏感 Header 返回 True，否则返回 False
    """
    lowered = header_name.strip().lower()
    return any(keyword in lowered for keyword in SENSITIVE_HEADER_KEYWORDS)


def mask_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """
    对 HTTP Header 字典中的敏感键值进行脱敏复制。

    @param headers: 原始 Header 字典
    @return: 脱敏后的新 Header 字典
    """
    if not headers:
        return {}
    masked: dict[str, str] = {}
    for key, val in headers.items():
        if is_sensitive_header(key):
            masked[key] = mask_secret_value(str(val))
        else:
            masked[key] = str(val)
    return masked


def mask_url_credentials(raw_url: str) -> str:
    """
    将 URL 中嵌入的用户名密码替换为掩码（适用于数据库连接串、代理地址与 Webhook URL）。

    @param raw_url: 原始 URL 字符串
    @return: 移除或掩码敏感凭据后的安全 URL
    """
    if not raw_url:
        return ""
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return _URL_CREDENTIALS_PATTERN.sub(r"\1******\3", raw_url)

    netloc = parsed.netloc
    if parsed.password is not None:
        user_part = parsed.username or ""
        host_part = parsed.hostname or ""
        if ":" in host_part and not host_part.startswith("["):
            host_part = f"[{host_part}]"
        port_part = f":{parsed.port}" if parsed.port else ""
        netloc = f"{user_part}:******@{host_part}{port_part}"

    sanitized_query = _QUERY_SECRET_PATTERN.sub(r"\1******", parsed.query)
    return urlunsplit((parsed.scheme, netloc, parsed.path, sanitized_query, parsed.fragment))


def sanitize_log_text(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    """
    清洗日志或错误文本中的常见凭据模式及显式传入的运行期密钥。

    @param text: 原始文本
    @param extra_secrets: 需要强制替换的运行期明文密钥集合
    @return: 脱敏后的安全文本
    """
    if not text:
        return ""
    cleaned = _URL_CREDENTIALS_PATTERN.sub(r"\1******\3", text)
    cleaned = _BEARER_PATTERN.sub(r"\1******", cleaned)
    cleaned = _QUERY_SECRET_PATTERN.sub(r"\1******", cleaned)
    for secret in extra_secrets:
        if secret and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "******")
    return cleaned


def verify_admin_token(expected_token: str, provided_token: str | None) -> bool:
    """
    使用常量时间比较校验管理接口令牌。

    @param expected_token: 系统配置的 ADMIN_TOKEN；为空表示无需鉴权
    @param provided_token: 请求端提供的令牌字符串
    @return: 鉴权通过返回 True，否则返回 False
    """
    if not expected_token:
        return True
    if not provided_token:
        return False
    return hmac.compare_digest(expected_token.encode("utf-8"), provided_token.encode("utf-8"))


def resolve_or_create_secret_key(configured_key: str, data_dir: Path) -> str:
    """
    获取应用 JWT 与密钥加密所使用的主密钥。

    若显式配置了 AUTH_SECRET_KEY 则直接返回；否则从 data_dir/.auth_secret 读取，
    文件不存在时生成 32 字节随机十六进制密钥并以原子临时文件替换写入持久化，
    避免容器重启导致已有会话与已绑定的 TOTP 密钥失效。

    @param configured_key: 静态配置传入的 auth_secret_key
    @param data_dir: 本地持久化数据目录路径
    @return: 非空主密钥字符串
    """
    cleaned = configured_key.strip()
    if cleaned:
        return cleaned

    data_dir.mkdir(parents=True, exist_ok=True)
    secret_path = data_dir / ".auth_secret"
    if secret_path.is_file():
        stored = secret_path.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    generated = secrets.token_hex(32)
    tmp_path = data_dir / f".auth_secret.{secrets.token_hex(4)}.tmp"
    tmp_path.write_text(generated, encoding="utf-8")
    tmp_path.replace(secret_path)
    return generated


def hash_password(password: str) -> str:
    """
    使用 Argon2id 对明文密码执行加盐单向哈希。

    @param password: 用户明文密码
    @return: Argon2id 编码后的哈希字符串
    """
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """
    校验明文密码与 Argon2id 哈希是否匹配，支持防用户名枚举的恒定耗时路径。

    @param password: 待校验明文密码
    @param password_hash: 数据库中存储的哈希值；为 None 时执行虚拟校验并返回 False
    @return: 匹配返回 True，否则返回 False
    """
    if not password_hash:
        try:
            _PASSWORD_HASHER.verify(_DUMMY_PASSWORD_HASH, password or "invalid")
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        return False

    try:
        return bool(_PASSWORD_HASHER.verify(password_hash, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _derive_fernet(master_key: str) -> Fernet:
    """
    从主密钥通过 SHA-256 派生 32 字节标准 Fernet 对称加密器。

    @param master_key: 应用主密钥
    @return: Fernet 加密器实例
    """
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str, master_key: str) -> str:
    """
    使用应用主密钥加密敏感字符串（如用户 TOTP Base32 密钥）以便安全落库。

    @param plaintext: 明文敏感字符串
    @param master_key: 应用主密钥
    @return: 加密后的密文 Token 字符串
    """
    fernet = _derive_fernet(master_key)
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, master_key: str) -> str:
    """
    使用应用主密钥解密数据库中存储的密文字符串。

    @param ciphertext: encrypt_secret 生成的密文 Token
    @param master_key: 应用主密钥
    @return: 解密后的原始明文
    @raise ValueError: 当密文损坏或主密钥不匹配时抛出
    """
    fernet = _derive_fernet(master_key)
    try:
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Failed to decrypt stored secret") from exc
