import html
import re
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Header
from sqlalchemy import func
from sqlalchemy.orm import Session
import jwt

from app.config import get_settings
from app.db.database import get_db
from app.db.models import Video, Analysis, VideoStats, ChannelBlacklist, User

settings = get_settings()
JWT_ALGORITHM = "HS256"


def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")

    user = db.query(User).filter(User.id == payload["user_id"]).first()
    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다")
    return user


def duration_to_seconds(iso_dur: str) -> int:
    """PT1H2M3S → 초 변환"""
    if not iso_dur:
        return 0
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso_dur)
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def filter_by_duration(items: list[dict], duration: str | None) -> list[dict]:
    """응답 dict 리스트에서 duration 필터 적용"""
    if not duration:
        return items
    result = []
    for item in items:
        secs = duration_to_seconds(item.get("duration", ""))
        if duration == "short" and secs <= 60:
            result.append(item)
        elif duration == "medium" and 60 < secs <= 1200:
            result.append(item)
        elif duration == "long" and secs > 1200:
            result.append(item)
    return result


PERIOD_DAYS = {
    "1d": 1,
    "1w": 7,
    "2w": 14,
    "1m": 30,
    "3m": 90,
    "6m": 180,
}


def filter_by_period(items: list[dict], period: str | None) -> list[dict]:
    """응답 dict 리스트에서 업로드 기간 필터 적용"""
    if not period or period not in PERIOD_DAYS:
        return items
    cutoff = datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS[period])
    result = []
    for item in items:
        pub = item.get("published_at")
        if pub is None:
            continue
        # datetime 또는 문자열 처리
        if isinstance(pub, str):
            try:
                pub = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        if pub >= cutoff:
            result.append(item)
    return result


def build_video_response(
    video: Video,
    analysis: Analysis | None = None,
    latest_stat: VideoStats | None = None,
) -> dict:
    return {
        "video_id": video.video_id,
        "title": html.unescape(video.title or ""),
        "channel_id": video.channel_id,
        "channel_title": video.channel_title or "",
        "description": video.description or "",
        "duration": video.duration or "",
        "tags": video.tags or "",
        "category_id": video.category_id or "",
        "subscriber_count": video.subscriber_count or 0,
        "thumbnail": video.thumbnail,
        "published_at": video.published_at,
        "views": latest_stat.views if latest_stat else 0,
        "likes": latest_stat.likes if latest_stat else 0,
        "comments": latest_stat.comments if latest_stat else 0,
        "vph": analysis.vph if analysis else 0,
        "score": analysis.score if analysis else 0,
        "predicted_views_24h": analysis.predicted_views_24h if analysis else 0,
        "predicted_views_7d": analysis.predicted_views_7d if analysis else 0,
        "growth_pattern": analysis.growth_pattern if analysis else "unknown",
        "is_darkhorse": analysis.is_darkhorse if analysis else False,
    }


def batch_latest_stats(db: Session, video_ids: list[str]) -> dict[str, VideoStats]:
    if not video_ids:
        return {}

    latest_subq = (
        db.query(
            VideoStats.video_id,
            func.max(VideoStats.collected_at).label("max_at"),
        )
        .filter(VideoStats.video_id.in_(video_ids))
        .group_by(VideoStats.video_id)
        .subquery()
    )
    latest_stats = (
        db.query(VideoStats)
        .join(
            latest_subq,
            (VideoStats.video_id == latest_subq.c.video_id)
            & (VideoStats.collected_at == latest_subq.c.max_at),
        )
        .all()
    )
    return {s.video_id: s for s in latest_stats}


def sanitize_csv_field(value) -> str:
    """CSV 인젝션 방지 — 수식 문자로 시작하는 값에 작은따옴표 추가"""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{s}"
    return s


def get_blacklisted_channel_ids(db: Session, user_id: int) -> set[str]:
    rows = db.query(ChannelBlacklist.channel_id).filter(
        ChannelBlacklist.user_id == user_id
    ).all()
    return {r[0] for r in rows}
