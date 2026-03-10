import html
import logging
import requests
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.db.models import Video
from app.api_key_pool import api_request

logger = logging.getLogger(__name__)

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


PERIOD_DAYS = {
    "1d": 1,
    "1w": 7,
    "2w": 14,
    "1m": 30,
    "3m": 90,
    "6m": 180,
}


def search_videos(keyword: str, max_results: int = 50, duration: str | None = None, period: str | None = None) -> list[dict]:
    """YouTube API로 키워드 검색하여 영상 목록 반환 (최대 50개)"""
    try:
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "order": "viewCount",
            "maxResults": min(max_results, 50),
        }
        if duration in ("short", "medium", "long"):
            params["videoDuration"] = duration
        if period in PERIOD_DAYS:
            after = datetime.now(timezone.utc) - timedelta(days=PERIOD_DAYS[period])
            params["publishedAfter"] = after.strftime("%Y-%m-%dT%H:%M:%SZ")

        response = api_request(YOUTUBE_SEARCH_URL, params)
        if not response or response.status_code != 200:
            return []
        data = response.json()

        videos = []
        video_ids = []
        for item in data.get("items", []):
            vid = item["id"].get("videoId")
            if not vid:
                continue
            snippet = item["snippet"]
            published = snippet.get("publishedAt", "")
            published_dt = None
            if published:
                published_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))

            channel_id = snippet.get("channelId", "")
            if not channel_id:
                continue
            video_ids.append(vid)
            videos.append({
                "video_id": vid,
                "title": html.unescape(snippet["title"]),
                "channel_id": channel_id,
                "channel_title": snippet.get("channelTitle", ""),
                "description": snippet.get("description", ""),
                "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                "published_at": published_dt,
            })

        # contentDetails + snippet + statistics 통합 조회 (API 1회)
        if video_ids:
            details = _fetch_video_details(video_ids)
            channel_ids = list(set(
                d["channel_id"] or v["channel_id"]
                for v, d in ((v, details.get(v["video_id"], {})) for v in videos)
                if v["channel_id"] or d.get("channel_id")
            ))
            sub_counts = _fetch_subscriber_counts(channel_ids) if channel_ids else {}

            for v in videos:
                d = details.get(v["video_id"], {})
                v["duration"] = d.get("duration", "")
                v["tags"] = d.get("tags", "")
                v["category_id"] = d.get("category_id", "")
                if d.get("description"):
                    v["description"] = d["description"]
                v["subscriber_count"] = sub_counts.get(v["channel_id"], 0)
                v["views"] = d.get("views", 0)
                v["likes"] = d.get("likes", 0)
                v["comments"] = d.get("comments", 0)

        return videos
    except Exception:
        logger.exception("search_videos failed for keyword=%s", keyword)
        return []


def _fetch_video_details(video_ids: list[str]) -> dict[str, dict]:
    """YouTube API로 영상 상세정보+통계 조회 (1회 호출로 통합)"""
    result = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            params = {
                "part": "contentDetails,snippet,statistics",
                "id": ",".join(batch),
            }
            resp = api_request(YOUTUBE_VIDEOS_URL, params)
            if resp and resp.status_code == 200:
                for item in resp.json().get("items", []):
                    snippet = item.get("snippet", {})
                    stats = item.get("statistics", {})
                    result[item["id"]] = {
                        "duration": item.get("contentDetails", {}).get("duration", ""),
                        "tags": ",".join(snippet.get("tags", [])),
                        "category_id": snippet.get("categoryId", ""),
                        "channel_id": snippet.get("channelId", ""),
                        "description": snippet.get("description", ""),
                        "views": int(stats.get("viewCount", 0)),
                        "likes": int(stats.get("likeCount", 0)),
                        "comments": int(stats.get("commentCount", 0)),
                    }
        except Exception:
            logger.exception("_fetch_video_details failed for batch starting at %d", i)
    return result


def _fetch_subscriber_counts(channel_ids: list[str], db: Session = None) -> dict[str, int]:
    """YouTube Channels API로 채널 구독자 수 조회 (DB 캐시 활용)"""
    result = {}
    unique_ids = list(set(channel_ids))

    # DB에 이미 구독자 수가 있는 채널은 캐시 사용
    if db:
        cached = db.query(Video.channel_id, Video.subscriber_count).filter(
            Video.channel_id.in_(unique_ids),
            Video.subscriber_count > 0,
        ).distinct().all()
        for ch_id, sub_count in cached:
            result[ch_id] = sub_count
        unique_ids = [cid for cid in unique_ids if cid not in result]

    if not unique_ids:
        return result

    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i:i + 50]
        try:
            params = {
                "part": "statistics",
                "id": ",".join(batch),
            }
            resp = api_request(YOUTUBE_CHANNELS_URL, params)
            if resp and resp.status_code == 200:
                for item in resp.json().get("items", []):
                    stats = item.get("statistics", {})
                    result[item["id"]] = int(stats.get("subscriberCount", 0))
        except Exception:
            logger.exception("_fetch_subscriber_counts failed")
    return result


_STATS_KEYS = {"views", "likes", "comments"}

def save_videos(db: Session, videos: list[dict]) -> int:
    """검색된 영상을 DB에 저장 (중복 시 채널명/설명 업데이트)"""
    count = 0
    for v in videos:
        existing = db.query(Video).filter(Video.video_id == v["video_id"]).first()
        if not existing:
            video = Video(**{k: val for k, val in v.items() if k not in _STATS_KEYS})
            db.add(video)
            count += 1
        else:
            if v.get("channel_title") and not existing.channel_title:
                existing.channel_title = v["channel_title"]
            if v.get("description") and (not existing.description or len(v["description"]) > len(existing.description or "")):
                existing.description = v["description"]
            if v.get("duration") and not existing.duration:
                existing.duration = v["duration"]
            if v.get("tags") and not existing.tags:
                existing.tags = v["tags"]
            if v.get("category_id") and not existing.category_id:
                existing.category_id = v["category_id"]
            if v.get("subscriber_count") and (not existing.subscriber_count or existing.subscriber_count == 0):
                existing.subscriber_count = v["subscriber_count"]
    db.commit()
    return count


def discover_and_store(db: Session, keyword: str, max_results: int = 50, duration: str | None = None, period: str | None = None) -> list[dict]:
    """키워드로 영상 검색 후 DB 저장 + 키워드 연결 + 통계 저장"""
    from app.db.models import VideoKeyword
    from app.crawler.stats_collector import _save_stats_and_analyze

    videos = search_videos(keyword, max_results, duration=duration, period=period)
    save_videos(db, videos)

    for v in videos:
        exists = db.query(VideoKeyword).filter(
            VideoKeyword.video_id == v["video_id"],
            VideoKeyword.keyword == keyword,
        ).first()
        if not exists:
            db.add(VideoKeyword(video_id=v["video_id"], keyword=keyword))
    try:
        db.commit()
    except Exception:
        db.rollback()

    # search_videos에서 이미 가져온 통계를 바로 저장 (추가 API 호출 없음)
    stats_list = [
        {"video_id": v["video_id"], "views": v.get("views", 0),
         "likes": v.get("likes", 0), "comments": v.get("comments", 0)}
        for v in videos if v.get("views", 0) > 0
    ]
    if stats_list:
        _save_stats_and_analyze(db, stats_list)

    return videos
