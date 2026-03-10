import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import func
from app.celery_app import celery
from app.db.database import SessionLocal
from app.crawler.stats_collector import collect_and_analyze
from app.crawler.youtube_search import discover_and_store
from app.db.models import SearchHistory, VideoStats, ChannelBookmark, ChannelNotification, Video

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
    """즐겨찾기 채널의 새 영상 감지 → 각 사용자에게 알림 생성"""
    db = SessionLocal()
    try:
        bookmarks = db.query(ChannelBookmark).all()
        if not bookmarks:
            return

        # 기존 알림을 배치로 조회 (N+1 방지)
        all_notifs = db.query(
            ChannelNotification.user_id,
            ChannelNotification.channel_id,
            ChannelNotification.video_id,
        ).all()
        existing_set = {(n.user_id, n.channel_id, n.video_id) for n in all_notifs}

        # 관련 채널의 영상을 배치로 조회
        channel_ids = list({cb.channel_id for cb in bookmarks})
        all_videos = db.query(Video).filter(Video.channel_id.in_(channel_ids)).all()
        videos_by_channel = {}
        for v in all_videos:
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
        logger.info("check_channel_new_videos: checked %d bookmarks", len(bookmarks))
    except Exception:
        db.rollback()
        logger.exception("check_channel_new_videos failed")
    finally:
        db.close()


@celery.task(name="app.tasks.cleanup_old_data")
def cleanup_old_data():
    """매일 오래된 통계 데이터 정리 (30일 이상)"""
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        deleted = db.query(VideoStats).filter(VideoStats.collected_at < cutoff).delete()
        db.commit()

        # 오래된 검색 기록도 정리 (90일 이상)
        search_cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        db.query(SearchHistory).filter(SearchHistory.searched_at < search_cutoff).delete()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("cleanup_old_data failed")
    finally:
        db.close()
