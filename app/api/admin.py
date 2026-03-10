"""관리자 전용 API"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.database import get_db
from app.db.models import (
    User, Video, VideoStats, Analysis, Bookmark,
    ChannelBlacklist, ChannelBookmark, ChannelNotification,
    VideoMemo, Reference, SearchHistory, WatchedKeyword,
)
from app.api.shared import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin")


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return current_user


@router.get("/users")
def list_users(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """전체 사용자 목록"""
    from app.crypto import decrypt
    users = db.query(User).order_by(User.created_at).all()
    result = []
    for u in users:
        has_key = bool(u.youtube_api_key and decrypt(u.youtube_api_key))
        result.append({
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "has_api_key": has_key,
            "created_at": u.created_at,
        })
    return result


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """사용자 삭제"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="관리자 계정은 삭제할 수 없습니다")

    logger.warning("Admin %s deleting user: %s (id=%d)", admin.username, user.username, user_id)
    db.query(Bookmark).filter(Bookmark.user_id == user_id).delete()
    db.query(ChannelBlacklist).filter(ChannelBlacklist.user_id == user_id).delete()
    db.query(ChannelNotification).filter(ChannelNotification.user_id == user_id).delete()
    db.query(ChannelBookmark).filter(ChannelBookmark.user_id == user_id).delete()
    db.query(VideoMemo).filter(VideoMemo.user_id == user_id).delete()
    db.query(Reference).filter(Reference.user_id == user_id).delete()
    db.query(WatchedKeyword).filter(WatchedKeyword.user_id == user_id).delete()
    db.query(User).filter(User.id == user_id).delete()
    db.commit()
    return {"ok": True}


@router.get("/stats")
def system_stats(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """시스템 현황"""
    total_users = db.query(User).count()
    total_videos = db.query(Video).count()
    total_stats = db.query(VideoStats).count()
    total_keywords = db.query(SearchHistory).count()
    api_key_users = db.query(User).filter(
        User.youtube_api_key != "",
        User.youtube_api_key.isnot(None),
    ).count()

    # 최근 수집 시각
    latest_stat = db.query(func.max(VideoStats.collected_at)).scalar()

    return {
        "total_users": total_users,
        "total_videos": total_videos,
        "total_stats_records": total_stats,
        "total_keywords": total_keywords,
        "api_key_users": api_key_users,
        "latest_collection": latest_stat,
    }


@router.get("/search-keywords")
def search_keywords(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """사용자별 검색 키워드 현황"""
    rows = (
        db.query(
            SearchHistory.user_id,
            User.username,
            SearchHistory.keyword,
            func.count(SearchHistory.id).label("cnt"),
            func.max(SearchHistory.searched_at).label("last_at"),
        )
        .outerjoin(User, SearchHistory.user_id == User.id)
        .group_by(SearchHistory.user_id, User.username, SearchHistory.keyword)
        .order_by(func.max(SearchHistory.searched_at).desc())
        .limit(500)
        .all()
    )
    result = []
    for r in rows:
        result.append({
            "username": r.username or "시스템",
            "keyword": r.keyword,
            "count": r.cnt,
            "last_searched": r.last_at,
        })
    return result
