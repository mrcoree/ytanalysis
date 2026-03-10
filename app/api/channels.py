import html as html_mod
import re
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import (
    ChannelBlacklist, ChannelBookmark, ChannelNotification, Video, Analysis, VideoStats, User,
)
from app.api.shared import batch_latest_stats, get_current_user
from app.api_key_pool import api_request

logger = logging.getLogger(__name__)
router = APIRouter()

YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


@router.get("/channel-bookmarks")
def get_channel_bookmarks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bookmarks = (
        db.query(ChannelBookmark)
        .filter(ChannelBookmark.user_id == current_user.id)
        .order_by(ChannelBookmark.created_at.desc())
        .all()
    )
    if not bookmarks:
        return {"channels": []}

    channel_ids = [cb.channel_id for cb in bookmarks]

    counts = dict(
        db.query(Video.channel_id, func.count(Video.video_id))
        .filter(Video.channel_id.in_(channel_ids))
        .group_by(Video.channel_id)
        .all()
    )

    avg_vphs = dict(
        db.query(Video.channel_id, func.avg(Analysis.vph))
        .join(Analysis, Video.video_id == Analysis.video_id)
        .filter(Video.channel_id.in_(channel_ids))
        .group_by(Video.channel_id)
        .all()
    )

    unread_counts = dict(
        db.query(
            ChannelNotification.channel_id,
            func.count(ChannelNotification.id),
        )
        .filter(
            ChannelNotification.user_id == current_user.id,
            ChannelNotification.channel_id.in_(channel_ids),
            ChannelNotification.is_read.is_(False),
        )
        .group_by(ChannelNotification.channel_id)
        .all()
    )

    result = []
    for cb in bookmarks:
        result.append({
            "channel_id": cb.channel_id,
            "channel_title": cb.channel_title,
            "thumbnail": cb.thumbnail,
            "video_count": counts.get(cb.channel_id, 0),
            "avg_vph": round(avg_vphs.get(cb.channel_id, 0) or 0, 2),
            "unread_count": unread_counts.get(cb.channel_id, 0),
            "created_at": cb.created_at,
        })

    return {"channels": result}


class ChannelAddRequest(BaseModel):
    url: str


def _resolve_channel_id(url_or_handle: str) -> dict | None:
    text = url_or_handle.strip()

    m = re.search(r'channel/(UC[\w-]+)', text)
    if m:
        channel_id = m.group(1)
        return _fetch_channel_by_id(channel_id)

    m = re.search(r'@([\w.\-]+)', text)
    if m:
        handle = m.group(1)
        return _fetch_channel_by_handle(handle)

    m = re.search(r'/(c|user)/([\w.\-]+)', text)
    if m:
        name = m.group(2)
        return _fetch_channel_by_handle(name)

    if text.startswith('UC') and len(text) >= 20:
        return _fetch_channel_by_id(text)

    return _fetch_channel_by_handle(text)


def _fetch_channel_by_id(channel_id: str) -> dict | None:
    try:
        resp = api_request(YOUTUBE_CHANNELS_URL, {
            "part": "snippet",
            "id": channel_id,
        })
        if not resp or resp.status_code != 200:
            return None
        items = resp.json().get("items", [])
        if not items:
            return None
        snippet = items[0]["snippet"]
        return {
            "channel_id": items[0]["id"],
            "channel_title": snippet.get("title", ""),
            "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
        }
    except Exception:
        logger.exception("_fetch_channel_by_id failed")
        return None


def _fetch_channel_by_handle(handle: str) -> dict | None:
    try:
        resp = api_request(YOUTUBE_CHANNELS_URL, {
            "part": "snippet",
            "forHandle": handle,
        })
        if resp and resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                snippet = items[0]["snippet"]
                return {
                    "channel_id": items[0]["id"],
                    "channel_title": snippet.get("title", ""),
                    "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                }

        resp = api_request(YOUTUBE_SEARCH_URL, {
            "part": "snippet",
            "q": handle,
            "type": "channel",
            "maxResults": 1,
        })
        if resp and resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                snippet = items[0]["snippet"]
                return {
                    "channel_id": snippet["channelId"],
                    "channel_title": snippet.get("channelTitle", snippet.get("title", "")),
                    "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                }
        return None
    except Exception:
        logger.exception("_fetch_channel_by_handle failed")
        return None


def _fetch_channel_videos(channel_id: str, max_results: int = 30) -> list[dict]:
    try:
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "type": "video",
            "order": "date",
            "maxResults": max_results,
        }
        resp = api_request(YOUTUBE_SEARCH_URL, params, timeout=15)
        if not resp or resp.status_code != 200:
            logger.error("Channel video search failed: %s", resp.text)
            return []

        items = resp.json().get("items", [])
        if not items:
            return []

        videos = []
        video_ids = []
        for item in items:
            snippet = item["snippet"]
            vid = item["id"]["videoId"]
            video_ids.append(vid)

            published = snippet.get("publishedAt", "")
            published_dt = None
            if published:
                published_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))

            videos.append({
                "video_id": vid,
                "title": html_mod.unescape(snippet.get("title", "")),
                "channel_id": snippet.get("channelId", channel_id),
                "channel_title": snippet.get("channelTitle", ""),
                "description": snippet.get("description", ""),
                "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                "published_at": published_dt,
            })

        if video_ids:
            from app.crawler.youtube_search import _fetch_video_details, _fetch_subscriber_counts
            details = _fetch_video_details(video_ids)
            sub_counts = _fetch_subscriber_counts([channel_id])
            sub_count = sub_counts.get(channel_id, 0)

            for v in videos:
                d = details.get(v["video_id"], {})
                v["duration"] = d.get("duration", "")
                v["tags"] = d.get("tags", "")
                v["category_id"] = d.get("category_id", "")
                v["subscriber_count"] = sub_count

        return videos
    except Exception:
        logger.exception("_fetch_channel_videos failed for %s", channel_id)
        return []


def _import_channel_videos(db: Session, channel_id: str):
    from app.crawler.stats_collector import collect_stats_for_videos

    videos = _fetch_channel_videos(channel_id)
    if not videos:
        return 0

    new_ids = []
    for v in videos:
        existing = db.query(Video).filter(Video.video_id == v["video_id"]).first()
        if not existing:
            db.add(Video(**v))
            new_ids.append(v["video_id"])
        else:
            if v.get("channel_title") and not existing.channel_title:
                existing.channel_title = v["channel_title"]
    db.commit()

    all_ids = [v["video_id"] for v in videos]
    collect_stats_for_videos(db, all_ids)

    return len(new_ids)


@router.post("/channel-bookmark/add-by-url")
def add_channel_by_url(
    req: ChannelAddRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    info = _resolve_channel_id(req.url)
    if not info:
        raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다")

    channel_id = info["channel_id"]

    existing = db.query(ChannelBookmark).filter(
        ChannelBookmark.user_id == current_user.id,
        ChannelBookmark.channel_id == channel_id,
    ).first()
    if existing:
        return {"ok": True, "action": "already_exists", "channel": info}

    cb = ChannelBookmark(
        user_id=current_user.id,
        channel_id=channel_id,
        channel_title=info["channel_title"],
        thumbnail=info["thumbnail"],
    )
    db.add(cb)
    db.commit()

    imported = _import_channel_videos(db, channel_id)
    info["imported_videos"] = imported

    return {"ok": True, "action": "added", "channel": info}


@router.post("/channel-bookmark/{channel_id}")
def add_channel_bookmark(
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(ChannelBookmark).filter(
        ChannelBookmark.user_id == current_user.id,
        ChannelBookmark.channel_id == channel_id,
    ).first()
    if existing:
        return {"ok": True, "action": "already_exists"}

    video = db.query(Video).filter(Video.channel_id == channel_id).first()
    info = _fetch_channel_by_id(channel_id)
    if info:
        channel_title = info["channel_title"]
        thumbnail = info["thumbnail"]
    elif video:
        channel_title = video.channel_title or ""
        thumbnail = ""
    else:
        channel_title = ""
        thumbnail = ""

    cb = ChannelBookmark(
        user_id=current_user.id,
        channel_id=channel_id,
        channel_title=channel_title,
        thumbnail=thumbnail,
    )
    db.add(cb)
    db.commit()

    _import_channel_videos(db, channel_id)

    return {"ok": True, "action": "added"}


@router.delete("/channel-bookmark/{channel_id}")
def remove_channel_bookmark(
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cb = db.query(ChannelBookmark).filter(
        ChannelBookmark.user_id == current_user.id,
        ChannelBookmark.channel_id == channel_id,
    ).first()
    if cb:
        db.delete(cb)
        db.query(ChannelNotification).filter(
            ChannelNotification.user_id == current_user.id,
            ChannelNotification.channel_id == channel_id,
        ).delete()
        db.commit()
    return {"ok": True, "action": "removed"}


@router.get("/notifications")
def get_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notifications = (
        db.query(ChannelNotification)
        .filter(ChannelNotification.user_id == current_user.id)
        .order_by(ChannelNotification.created_at.desc())
        .limit(50)
        .all()
    )

    if not notifications:
        return {"notifications": [], "unread_total": 0}

    video_ids = [n.video_id for n in notifications]
    videos = db.query(Video).filter(Video.video_id.in_(video_ids)).all()
    video_map = {v.video_id: v for v in videos}

    stats_map = batch_latest_stats(db, video_ids)

    channel_ids = list({n.channel_id for n in notifications})
    ch_bookmarks = (
        db.query(ChannelBookmark)
        .filter(
            ChannelBookmark.user_id == current_user.id,
            ChannelBookmark.channel_id.in_(channel_ids),
        )
        .all()
    )
    ch_map = {cb.channel_id: cb.channel_title for cb in ch_bookmarks}

    result = []
    for n in notifications:
        video = video_map.get(n.video_id)
        if not video:
            continue
        latest = stats_map.get(n.video_id)
        result.append({
            "id": n.id,
            "channel_id": n.channel_id,
            "channel_title": ch_map.get(n.channel_id, ""),
            "video_id": n.video_id,
            "title": html_mod.unescape(video.title or ""),
            "thumbnail": video.thumbnail,
            "views": latest.views if latest else 0,
            "published_at": video.published_at,
            "is_read": n.is_read,
            "created_at": n.created_at,
        })

    unread_total = sum(1 for n in notifications if not n.is_read)

    return {"notifications": result, "unread_total": unread_total}


@router.post("/notification/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    n = db.query(ChannelNotification).filter(
        ChannelNotification.id == notification_id,
        ChannelNotification.user_id == current_user.id,
    ).first()
    if n:
        n.is_read = True
        db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.query(ChannelNotification).filter(
        ChannelNotification.user_id == current_user.id,
        ChannelNotification.is_read.is_(False),
    ).update({"is_read": True})
    db.commit()
    return {"ok": True}


# ===== 채널 블랙리스트 =====

@router.get("/channel-blacklist")
def get_blacklist(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = db.query(ChannelBlacklist).filter(
        ChannelBlacklist.user_id == current_user.id,
    ).order_by(ChannelBlacklist.created_at.desc()).all()
    return {
        "channels": [
            {"id": b.id, "channel_id": b.channel_id, "channel_title": b.channel_title, "created_at": b.created_at}
            for b in items
        ]
    }


@router.post("/channel-blacklist/{channel_id}")
def add_to_blacklist(
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(ChannelBlacklist).filter(
        ChannelBlacklist.user_id == current_user.id,
        ChannelBlacklist.channel_id == channel_id,
    ).first()
    if existing:
        return {"ok": True, "message": "이미 블랙리스트에 있습니다"}

    channel_title = ""
    video = db.query(Video).filter(Video.channel_id == channel_id).first()
    if video:
        channel_title = video.channel_title or ""

    db.add(ChannelBlacklist(user_id=current_user.id, channel_id=channel_id, channel_title=channel_title))
    db.commit()
    return {"ok": True}


@router.delete("/channel-blacklist/{channel_id}")
def remove_from_blacklist(
    channel_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deleted = db.query(ChannelBlacklist).filter(
        ChannelBlacklist.user_id == current_user.id,
        ChannelBlacklist.channel_id == channel_id,
    ).delete()
    db.commit()
    return {"ok": True, "deleted": deleted}
