import secrets
from urllib.parse import urlparse
from pydantic_settings import BaseSettings
from functools import lru_cache




class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://viral:viral@postgres:5432/viral_radar"
    REDIS_URL: str = "redis://redis:6379/0"
    YOUTUBE_API_KEY: str = ""
    SUBTITLE_PROXY_URL: str = ""
    JWT_SECRET: str = ""
    INVITE_CODE: str = "1021"
    ENCRYPTION_KEY: str = ""
    ALLOWED_ORIGINS: str = ""  # 콤마 구분, 비어있으면 same-origin만

    class Config:
        env_file = ".env"
        extra = "ignore"

    def get_jwt_secret(self) -> str:
        if not self.JWT_SECRET:
            raise RuntimeError(
                "JWT_SECRET 환경변수가 설정되지 않았습니다. "
                ".env 파일에 JWT_SECRET=<랜덤 문자열>을 추가하세요."
            )
        return self.JWT_SECRET

    def get_validated_proxy_url(self) -> str:
        """SUBTITLE_PROXY_URL 검증 — HTTPS만 허용"""
        url = self.SUBTITLE_PROXY_URL
        if not url:
            return ""
        parsed = urlparse(url)
        if parsed.scheme not in ("https",):
            return ""
        return url


@lru_cache()
def get_settings() -> Settings:
    return Settings()
