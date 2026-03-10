"""YouTube API 키 풀 관리

모든 사용자의 API 키를 DB에서 로드하여 풀링.
매 요청마다 다른 키를 사용하여 공평하게 분산.
403(할당량 초과)된 키는 자동 스킵.
"""
import logging
import threading
import time
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_key_index = 0
# 할당량 초과된 키 → 만료 시각 (자정에 리셋되므로 당일만 스킵)
_exhausted_keys: dict[str, float] = {}


def get_all_api_keys() -> list[str]:
    """DB의 모든 사용자 키를 반환 (복호화 포함)"""
    from app.db.models import User
    from app.crypto import decrypt
    keys = []

    try:
        db = SessionLocal()
        try:
            user_keys = db.query(User.youtube_api_key).filter(
                User.youtube_api_key != "",
                User.youtube_api_key.isnot(None),
            ).all()
            for (k,) in user_keys:
                if not k:
                    continue
                decrypted = decrypt(k)
                if decrypted and decrypted not in keys:
                    keys.append(decrypted)
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to load user API keys")

    return keys


def _next_key(keys: list[str]) -> str | None:
    """라운드 로빈으로 다음 키를 선택. 소진된 키는 건너뜀."""
    global _key_index
    now = time.time()

    # 만료된 소진 기록 정리 (6시간 지나면 다시 시도)
    expired = [k for k, t in _exhausted_keys.items() if now - t > 6 * 3600]
    for k in expired:
        del _exhausted_keys[k]

    with _lock:
        for _ in range(len(keys)):
            idx = _key_index % len(keys)
            _key_index += 1
            key = keys[idx]
            if key not in _exhausted_keys:
                return key

    return None


def api_request(url: str, params: dict, timeout: int = 10):
    """매 요청마다 키를 돌아가며 사용. 403 시 해당 키만 스킵."""
    import requests
    keys = get_all_api_keys()
    if not keys:
        return None

    # 1차: 라운드 로빈으로 키 선택
    key = _next_key(keys)
    if not key:
        logger.error("All %d API keys exhausted", len(keys))
        return None

    params["key"] = key
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code == 403:
            reason = ""
            try:
                reason = resp.json().get("error", {}).get("errors", [{}])[0].get("reason", "")
            except Exception:
                pass
            if "quotaExceeded" in reason or "dailyLimitExceeded" in reason:
                logger.warning("API key exhausted, marking as used up")
                _exhausted_keys[key] = time.time()
                # 다른 키로 재시도
                return _retry_with_next(url, params, keys, timeout)
        return resp
    except requests.exceptions.Timeout:
        logger.warning("API request timeout")
        return _retry_with_next(url, params, keys, timeout)
    except Exception:
        logger.exception("API request failed")
        return None


def _retry_with_next(url: str, params: dict, keys: list[str], timeout: int):
    """다음 사용 가능한 키로 1회 재시도"""
    import requests
    key = _next_key(keys)
    if not key:
        logger.error("No available API keys for retry")
        return None

    params["key"] = key
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code == 403:
            try:
                reason = resp.json().get("error", {}).get("errors", [{}])[0].get("reason", "")
            except Exception:
                reason = ""
            if "quotaExceeded" in reason or "dailyLimitExceeded" in reason:
                _exhausted_keys[key] = time.time()
        return resp
    except Exception:
        logger.exception("API retry failed")
        return None
