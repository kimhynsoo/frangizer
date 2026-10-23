import hmac
import os
from dataclasses import dataclass

import jwt

from .errors import ServiceError
from .schemas import Principal


@dataclass(frozen=True)
class AuthSettings:
    internal_token: str
    jwt_secret: str
    issuer: str
    audience: str
    algorithm: str = "HS256"

    def __post_init__(self):
        if not all((self.internal_token, self.jwt_secret, self.issuer, self.audience)):
            raise ValueError("INTERNAL_TOKEN, JWT_SECRET, JWT_ISSUER, JWT_AUDIENCE are required")
        if self.algorithm != "HS256":
            raise ValueError("This initial adapter supports configured HS256 only")
        if len(self.jwt_secret.encode()) < 32:
            raise ValueError("JWT_SECRET must have at least 32 UTF-8 bytes")

    @classmethod
    def from_env(cls):
        return cls(*(os.environ.get(k, "") for k in (
            "INTERNAL_TOKEN", "JWT_SECRET", "JWT_ISSUER", "JWT_AUDIENCE"
        )), algorithm=os.environ.get("JWT_ALGORITHM") or "HS256")


def check_service_token(token: str | None, settings: AuthSettings):
    if token is None or not hmac.compare_digest(token.encode(), settings.internal_token.encode()):
        raise ServiceError(401, "AUTH_REQUIRED", "서비스 인증이 필요합니다.")


def check_jwt(authorization: str | None, settings: AuthSettings) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise ServiceError(401, "AUTH_REQUIRED", "사용자 인증이 필요합니다.")
    try:
        return jwt.decode(
            authorization[7:], settings.jwt_secret, algorithms=[settings.algorithm],
            issuer=settings.issuer, audience=settings.audience,
            options={"require": ["sub", "exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise ServiceError(401, "TOKEN_EXPIRED", "로그인이 만료되었습니다.") from None
    except jwt.InvalidTokenError:
        raise ServiceError(401, "AUTH_REQUIRED", "유효한 사용자 인증이 필요합니다.") from None


def check_principal(principal: Principal, claims: dict):
    permissions = claims.get("permissions")
    matches = (
        claims.get("sub") == principal.user_id
        and claims.get("role") == principal.role
        and claims.get("brand_id") == principal.brand_id
        and "store_id" in claims and claims["store_id"] == principal.store_id
        and isinstance(permissions, list) and all(isinstance(x, str) for x in permissions)
        and set(permissions) == set(principal.permissions)
        and "rag:ask" in principal.permissions
    )
    if not matches:
        raise ServiceError(403, "FORBIDDEN", "사용자와 요청 권한 범위가 일치하지 않습니다.")
