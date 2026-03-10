"""API 키 암호화/복호화 유틸리티 (Fernet 대칭 암호화)"""
import logging
from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings

logger = logging.getLogger(__name__)

_fernet = None
# 암호화된 값의 접두사 (평문과 구분)
_PREFIX = "enc:"


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        settings = get_settings()
        if not settings.ENCRYPTION_KEY:
            raise RuntimeError(
                "ENCRYPTION_KEY 환경변수가 설정되지 않았습니다. "
                ".env 파일에 ENCRYPTION_KEY를 추가하세요."
            )
        try:
            _fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        except (ValueError, Exception) as e:
            raise RuntimeError(
                f"ENCRYPTION_KEY 포맷이 잘못되었습니다: {e}. "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" 로 새 키를 생성하세요."
            )
    return _fernet


def encrypt(plaintext: str) -> str:
    """평문 → 암호화 문자열 (enc: 접두사 포함)"""
    if not plaintext:
        return ""
    f = _get_fernet()
    encrypted = f.encrypt(plaintext.encode()).decode()
    return f"{_PREFIX}{encrypted}"


def decrypt(stored: str) -> str:
    """저장된 값 → 평문. 암호화되지 않은 레거시 값도 처리."""
    if not stored:
        return ""
    # 암호화된 값
    if stored.startswith(_PREFIX):
        try:
            f = _get_fernet()
            return f.decrypt(stored[len(_PREFIX):].encode()).decode()
        except InvalidToken:
            logger.error("Failed to decrypt API key — invalid token")
            return ""
    # 레거시 평문 값 (마이그레이션 전 데이터)
    return stored
