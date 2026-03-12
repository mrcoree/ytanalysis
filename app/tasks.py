import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import func
from app.celery_app import celery
from app.db.database import SessionLocal
from app.crawler.stats_collector import collect_and_analyze
from app.crawler.youtube_search import discover_and_store
from app.db.models import SearchHistory, VideoStats, ChannelBookmark, ChannelNotification, Video, WatchedKeyword

logger = logging.getLogger(__name__)

# 기본 검색 키워드
DEFAULT_KEYWORDS = ["트렌드", "핫클립"]


@celery.task(name="app.tasks.collect_all_stats")
def collect_all_stats():
    """30분마다 최근 7일 영상 통계 수집"""
    db = SessionLocal()
    try:
        collect_and_analyze(db, tier="recent")
    except Exception:
        db.rollback()
        logger.exception("collect_all_stats failed")
    finally:
        db.close()


@celery.task(name="app.tasks.collect_mid_stats")
def collect_mid_stats():
    """2시간마다 7~30일 영상 통계 수집"""
    db = SessionLocal()
    try:
        collect_and_analyze(db, tier="mid")
    except Exception:
        db.rollback()
        logger.exception("collect_mid_stats failed")
    finally:
        db.close()


@celery.task(name="app.tasks.collect_old_stats")
def collect_old_stats():
    """하루 1회 30일+ 영상 통계 수집"""
    db = SessionLocal()
    try:
        collect_and_analyze(db, tier="old")
    except Exception:
        db.rollback()
        logger.exception("collect_old_stats failed")
    finally:
        db.close()


_discover_round = 0  # 라운드 로빈 카운터

@celery.task(name="app.tasks.discover_trending")
def discover_trending():
    """주기적으로 인기 키워드로 새 영상 발견

    매 실행마다:
    - 상위 3개 인기 키워드 (항상)
    - 나머지 키워드 중 1개 라운드 로빈 (매 실행마다 다른 키워드)
    → 최대 4개 키워드 = 쿼터 ~408/시간
    """
    global _discover_round
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        all_keywords = (
            db.query(SearchHistory.keyword, func.count(SearchHistory.id).label("cnt"))
            .filter(SearchHistory.searched_at >= cutoff)
            .group_by(SearchHistory.keyword)
            .order_by(func.count(SearchHistory.id).desc())
            .all()
        )

        if not all_keywords:
            # 검색 기록 없으면 기본 키워드만
            for kw in DEFAULT_KEYWORDS:
                discover_and_store(db, kw, max_results=30)
            return

        # 상위 3개는 매번 검색
        top_keywords = [kw for kw, _ in all_keywords[:3]]
        # 나머지 중 1개를 라운드 로빈
        rest_keywords = [kw for kw, _ in all_keywords[3:]]

        keywords_to_search = list(top_keywords)
        if rest_keywords:
            pick = rest_keywords[_discover_round % len(rest_keywords)]
            keywords_to_search.append(pick)
            _discover_round += 1

        for keyword in keywords_to_search:
            discover_and_store(db, keyword, max_results=30)
    except Exception:
        db.rollback()
        logger.exception("discover_trending failed")
    finally:
        db.close()


@celery.task(name="app.tasks.check_channel_new_videos")
def check_channel_new_videos():
    """즐겨찾기 채널 RSS로 새 영상 감지 → DB 저장 + 알림 생성 (API 최소화)"""
    import html as html_mod
    import requests
    from xml.etree import ElementTree
    from app.crawler.youtube_search import _fetch_video_details, save_videos, _fetch_subscriber_counts
    from app.crawler.stats_collector import _save_stats_and_analyze

    db = SessionLocal()
    try:
        bookmarks = db.query(ChannelBookmark).all()
        if not bookmarks:
            return

        # 고유 채널 목록
        channel_ids = list({cb.channel_id for cb in bookmarks})
        logger.info("check_channel_new_videos: RSS checking %d channels", len(channel_ids))

        # 1) RSS로 각 채널의 최신 영상 ID 수집 (무료)
        all_rss_videos = {}  # video_id -> basic info from RSS
        for ch_id in channel_ids:
            try:
                rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
                resp = requests.get(rss_url, timeout=10)
                if resp.status_code != 200:
                    continue
                root = ElementTree.fromstring(resp.content)
                ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "media": "http://search.yahoo.com/mrss/"}
                for entry in root.findall("atom:entry", ns):
                    vid = entry.find("yt:videoId", ns)
                    if vid is None:
                        continue
                    title_el = entry.find("atom:title", ns)
                    published_el = entry.find("atom:published", ns)
                    all_rss_videos[vid.text] = {
                        "channel_id": ch_id,
                        "title": html_mod.unescape(title_el.text) if title_el is not None else "",
                        "published": published_el.text if published_el is not None else "",
                    }
            except Exception:
                logger.exception("RSS fetch failed for channel %s", ch_id)

        # DB에 이미 있는 영상을 일괄 조회하여 필터링 (N+1 방지)
        if all_rss_videos:
            existing_ids = {v[0] for v in db.query(Video.video_id).filter(
                Video.video_id.in_(list(all_rss_videos.keys()))
            ).all()}
            rss_videos = {vid: info for vid, info in all_rss_videos.items() if vid not in existing_ids}
            new_video_ids = list(rss_videos.keys())
        else:
            rss_videos = {}
            new_video_ids = []

        # 2) 새 영상이 있으면 API로 상세정보 조회 후 DB 저장
        if new_video_ids:
            logger.info("check_channel_new_videos: %d new videos found via RSS", len(new_video_ids))
            details = _fetch_video_details(new_video_ids)

            # 구독자 수 조회
            detail_channel_ids = list({d.get("channel_id", "") for d in details.values() if d.get("channel_id")})
            sub_counts = _fetch_subscriber_counts(detail_channel_ids) if detail_channel_ids else {}

            videos_to_save = []
            for vid in new_video_ids:
                rss = rss_videos.get(vid, {})
                d = details.get(vid, {})
                ch_id = d.get("channel_id") or rss.get("channel_id", "")
                published_dt = None
                published_str = rss.get("published", "")
                if published_str:
                    try:
                        published_dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                videos_to_save.append({
                    "video_id": vid,
                    "title": rss.get("title", "") or d.get("description", "")[:100],
                    "channel_id": ch_id,
                    "channel_title": "",
                    "description": d.get("description", ""),
                    "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "published_at": published_dt,
                    "duration": d.get("duration", ""),
                    "tags": d.get("tags", ""),
                    "category_id": d.get("category_id", ""),
                    "subscriber_count": sub_counts.get(ch_id, 0),
                    "views": d.get("views", 0),
                    "likes": d.get("likes", 0),
                    "comments": d.get("comments", 0),
                })

            save_videos(db, videos_to_save)

            # 통계 저장 + 분석
            stats_list = [
                {"video_id": v["video_id"], "views": v["views"], "likes": v["likes"], "comments": v["comments"]}
                for v in videos_to_save if v["views"] > 0
            ]
            if stats_list:
                _save_stats_and_analyze(db, stats_list)

        # 3) 알림 생성 (새 영상 + 기존 DB 영상 모두)
        all_notifs = db.query(
            ChannelNotification.user_id,
            ChannelNotification.channel_id,
            ChannelNotification.video_id,
        ).all()
        existing_set = {(n.user_id, n.channel_id, n.video_id) for n in all_notifs}

        all_db_videos = db.query(Video).filter(Video.channel_id.in_(channel_ids)).all()
        videos_by_channel = {}
        for v in all_db_videos:
            videos_by_channel.setdefault(v.channel_id, []).append(v)

        for cb in bookmarks:
            channel_videos = videos_by_channel.get(cb.channel_id, [])
            for video in channel_videos:
                if (cb.user_id, cb.channel_id, video.video_id) not in existing_set:
                    db.add(ChannelNotification(
                        user_id=cb.user_id,
                        channel_id=cb.channel_id,
                        video_id=video.video_id,
                    ))
            if not cb.channel_title and channel_videos:
                first = channel_videos[0]
                if first.channel_title:
                    cb.channel_title = first.channel_title
                if first.thumbnail:
                    cb.thumbnail = first.thumbnail

        db.commit()
        logger.info("check_channel_new_videos: done, %d new videos added", len(new_video_ids))
    except Exception:
        db.rollback()
        logger.exception("check_channel_new_videos failed")
    finally:
        db.close()


@celery.task(name="app.tasks.auto_search_watched_keywords")
def auto_search_watched_keywords():
    """관심 키워드 자동 검색 — 모든 사용자의 등록 키워드를 검색하여 새 영상 발견"""
    db = SessionLocal()
    try:
        watched = db.query(WatchedKeyword).all()
        if not watched:
            return

        # 사용자별로 그룹핑하여 SearchHistory에 user_id 연결
        user_keywords = {}
        for wk in watched:
            user_keywords.setdefault(wk.user_id, []).append(wk.keyword)

        # 중복 제거된 키워드 목록
        unique_keywords = list({wk.keyword for wk in watched})
        logger.info("auto_search_watched_keywords: %d keywords from %d users",
                     len(unique_keywords), len(user_keywords))

        for keyword in unique_keywords:
            try:
                discover_and_store(db, keyword, max_results=30)
            except Exception:
                db.rollback()
                logger.exception("auto_search_watched failed for keyword=%s", keyword)

        # 각 사용자의 SearchHistory에 기록 (개인화 연결용)
        for user_id, keywords in user_keywords.items():
            for kw in keywords:
                db.add(SearchHistory(keyword=kw, user_id=user_id))
        try:
            db.commit()
        except Exception:
            db.rollback()

    except Exception:
        db.rollback()
        logger.exception("auto_search_watched_keywords failed")
    finally:
        db.close()


@celery.task(name="app.tasks.cleanup_old_data")
def cleanup_old_data():
    """매일 30일+ 통계를 일별 1개로 압축 + 90일+ 검색기록 정리"""
    from sqlalchemy import func as sa_func, cast, Date

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        # 30일 이전 데이터가 있는 영상 목록
        old_video_ids = [
            v[0] for v in db.query(VideoStats.video_id)
            .filter(VideoStats.collected_at < cutoff)
            .distinct()
            .all()
        ]

        if old_video_ids:
            compressed = 0
            deleted = 0
            for vid in old_video_ids:
                # 해당 영상의 30일 이전 데이터를 날짜별로 그룹핑
                daily_stats = (
                    db.query(
                        cast(VideoStats.collected_at, Date).label("day"),
                        sa_func.max(VideoStats.views).label("views"),
                        sa_func.max(VideoStats.likes).label("likes"),
                        sa_func.max(VideoStats.comments).label("comments"),
                    )
                    .filter(VideoStats.video_id == vid, VideoStats.collected_at < cutoff)
                    .group_by(cast(VideoStats.collected_at, Date))
                    .all()
                )

                # 날짜별 대표 1개 데이터 생성
                daily_entries = []
                for row in daily_stats:
                    daily_entries.append(VideoStats(
                        video_id=vid,
                        views=row.views,
                        likes=row.likes,
                        comments=row.comments,
                        collected_at=datetime.combine(row.day, datetime.min.time()).replace(hour=23, minute=59, tzinfo=timezone.utc),
                    ))

                # 기존 30일 이전 데이터 삭제
                del_count = db.query(VideoStats).filter(
                    VideoStats.video_id == vid,
                    VideoStats.collected_at < cutoff,
                ).delete()
                deleted += del_count

                # 압축된 일별 데이터 삽입
                for entry in daily_entries:
                    db.add(entry)
                    compressed += 1

            db.commit()
            logger.info("cleanup_old_data: compressed %d old records into %d daily summaries",
                        deleted, compressed)

        # 오래된 검색 기록 정리 (90일 이상)
        search_cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        db.query(SearchHistory).filter(SearchHistory.searched_at < search_cutoff).delete()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("cleanup_old_data failed")
    finally:
        db.close()
