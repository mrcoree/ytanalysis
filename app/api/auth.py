import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
import bcrypt
import jwt
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings
from app.db.database import get_db
from app.db.models import (
    User, Bookmark, ChannelBlacklist, ChannelBookmark,
    ChannelNotification, VideoMemo, Reference,
)
from app.api.shared import get_current_user, JWT_ALGORITHM

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

JWT_EXPIRE_HOURS = 24

# === 로그인 실패 잠금 ===
_login_failures: dict[str, list[float]] = {}
_MAX_FAILURES = 10
_LOCKOUT_SECONDS = 600  # 10분


def _check_login_lockout(username: str):
    """로그인 시도 횟수 확인 — 10회 실패 시 10분 잠금"""
    now = time.time()
    attempts = _login_failures.get(username, [])
    # 잠금 시간 지난 기록 제거
    fresh = [t for t in attempts if now - t < _LOCKOUT_SECONDS]
    if fresh:
        _login_failures[username] = fresh
    elif username in _login_failures:
        del _login_failures[username]
    if len(fresh) >= _MAX_FAILURES:
        raise HTTPException(
            status_code=429,
            detail="로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요.",
        )


def _record_login_failure(username: str):
    if username not in _login_failures:
        _login_failures[username] = []
    _login_failures[username].append(time.time())


def _clear_login_failures(username: str):
    _login_failures.pop(username, None)


class SignupRequest(BaseModel):
    username: str
    password: str
    invite_code: str


class LoginRequest(BaseModel):
    username: str
    password: str


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _create_token(user_id: int, username: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.get_jwt_secret(), algorithm=JWT_ALGORITHM)


@router.post("/auth/signup")
@limiter.limit("5/minute")
def signup(req: SignupRequest, request: Request, db: Session = Depends(get_db)):
    if req.invite_code != settings.INVITE_CODE:
        logger.warning("Signup failed: invalid invite code from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=403, detail="초대 코드가 올바르지 않습니다")

    username = req.username.strip()
    if len(username) < 2:
        raise HTTPException(status_code=400, detail="사용자명은 2자 이상이어야 합니다")
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="비밀번호는 4자 이상이어야 합니다")

    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(status_code=409, detail="이미 사용 중인 사용자명입니다")

    user = User(username=username, password_hash=_hash_password(req.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("New user registered: %s", username)
    token = _create_token(user.id, user.username)
    return {"ok": True, "token": token, "username": user.username}


@router.post("/auth/login")
@limiter.limit("10/minute")
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    username = req.username.strip()
    _check_login_lockout(username)

    user = db.query(User).filter(User.username == username).first()
    if not user or not _verify_password(req.password, user.password_hash):
        _record_login_failure(username)
        logger.warning("Login failed for user '%s' from %s", username, request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="사용자명 또는 비밀번호가 올바르지 않습니다")

    _clear_login_failures(username)
    logger.info("User logged in: %s", username)
    token = _create_token(user.id, user.username)
    return {"ok": True, "token": token, "username": user.username}


@router.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {"user_id": current_user.id, "username": current_user.username, "is_admin": current_user.is_admin}


# ===== 비밀번호 변경 =====

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


@router.put("/auth/password")
def change_password(
    req: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _verify_password(req.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다")
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="새 비밀번호는 4자 이상이어야 합니다")
    current_user.password_hash = _hash_password(req.new_password)
    db.commit()
    logger.info("Password changed for user: %s", current_user.username)
    return {"ok": True}


# ===== 설정 =====

class SettingsUpdate(BaseModel):
    youtube_api_key: str | None = None


@router.get("/settings")
def get_settings_api(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.crypto import decrypt
    raw_key = decrypt(current_user.youtube_api_key or "")
    # 마스킹: 앞 8자만 보여주고 나머지 ***
    masked = (raw_key[:8] + "***") if len(raw_key) > 8 else raw_key
    return {
        "username": current_user.username,
        "youtube_api_key": masked,
        "has_api_key": bool(raw_key),
        "created_at": current_user.created_at,
    }


@router.put("/settings")
def update_settings(
    req: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.youtube_api_key is not None:
        key = req.youtube_api_key.strip()
        if key:
            from app.crypto import encrypt
            current_user.youtube_api_key = encrypt(key)
        else:
            current_user.youtube_api_key = ""
    db.commit()
    return {"ok": True}


@router.get("/settings/api-keys-count")
def get_api_keys_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """등록된 API 키 수 (전체 사용자)"""
    count = db.query(User).filter(
        User.youtube_api_key != "",
        User.youtube_api_key.isnot(None),
    ).count()
    return {"user_keys": count, "total": count}


@router.delete("/auth/account")
def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """회원 탈퇴 — 사용자의 모든 개인 데이터 삭제"""
    uid = current_user.id
    logger.warning("Account deletion for user: %s (id=%d)", current_user.username, uid)
    db.query(Bookmark).filter(Bookmark.user_id == uid).delete()
    db.query(ChannelBlacklist).filter(ChannelBlacklist.user_id == uid).delete()
    db.query(ChannelNotification).filter(ChannelNotification.user_id == uid).delete()
    db.query(ChannelBookmark).filter(ChannelBookmark.user_id == uid).delete()
    db.query(VideoMemo).filter(VideoMemo.user_id == uid).delete()
    db.query(Reference).filter(Reference.user_id == uid).delete()
    db.query(User).filter(User.id == uid).delete()
    db.commit()
    return {"ok": True}
