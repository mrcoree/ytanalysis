import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.db.models import Video, VideoStats, Analysis
from app.analysis.vph import calculate_vph
from app.analysis.viral_score import calculate_viral_score
from app.analysis.predictor import predict_views
from app.analysis.growth_pattern import classify_growth_pattern
from app.analysis.darkhorse import detect_darkhorse
from app.api_key_pool import api_request

logger = logging.getLogger(__name__)

YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def fetch_video_stats(video_ids: list[str]) -> list[dict]:
    """YouTube API로 영상 통계 조회 (최대 50개씩)"""
    try:
        results = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            params = {
                "part": "statistics,snippet",
                "id": ",".join(batch),
            }
            response = api_request(YOUTUBE_VIDEOS_URL, params)
            if not response:
                continue
            response.raise_for_status()
            data = response.json()

            for item in data.get("items", []):
                stats = item["statistics"]
                snippet = item.get("snippet", {})
                results.append({
                    "video_id": item["id"],
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                    "description": snippet.get("description", ""),
                })
        return results
    except Exception:
        logger.exception("fetch_video_stats failed")
        return []


def _save_stats_and_analyze(db: Session, stats_list: list[dict]):
    """통계 저장 + VPH/바이럴 스코어 계산"""
    now = datetime.now(timezone.utc)

    for stat in stats_list:
        video_stat = VideoStats(
            video_id=stat["video_id"],
            views=stat["views"],
            likes=stat["likes"],
            comments=stat["comments"],
            collected_at=now,
        )
        db.add(video_stat)
    try:
        db.commit()
    except Exception:
        db.rollback()
        return

    # VPH, 바이럴 스코어, 예측, 성장패턴, 다크호스 계산
    from sqlalchemy import func as sa_func
    avg_vph_val = db.query(sa_func.avg(Analysis.vph)).scalar() or 0

    # 구독자 수 조회를 위한 영상 맵
    vid_list = [s["video_id"] for s in stats_list]
    video_map = {v.video_id: v for v in db.query(Video).filter(Video.video_id.in_(vid_list)).all()}

    for stat in stats_list:
        vid = stat["video_id"]
        video_obj = video_map.get(vid)
        if not video_obj:
            continue
        # 설명이 잘려있으면 전체 설명으로 업데이트
        new_desc = stat.get("description", "")
        if new_desc and video_obj and (
            not video_obj.description
            or len(new_desc) > len(video_obj.description or "")
        ):
            video_obj.description = new_desc
        sub_count = video_obj.subscriber_count if video_obj.subscriber_count else 0
        vph = calculate_vph(db, vid)
        score = calculate_viral_score(
            vph=vph,
            views=stat["views"],
            likes=stat["likes"],
            comments=stat["comments"],
            subscriber_count=sub_count,
        )
        predicted_24h, predicted_7d = predict_views(db, vid, stat["views"], vph)
        pattern = classify_growth_pattern(db, vid)
        is_darkhorse = detect_darkhorse(db, vid, vph, avg_vph=avg_vph_val)

        analysis = db.query(Analysis).filter(Analysis.video_id == vid).first()
        if analysis:
            analysis.vph = vph
            analysis.score = score
            analysis.predicted_views_24h = predicted_24h
            analysis.predicted_views_7d = predicted_7d
            analysis.growth_pattern = pattern
            analysis.is_darkhorse = is_darkhorse
        else:
            analysis = Analysis(
                video_id=vid, vph=vph, score=score,
                predicted_views_24h=predicted_24h,
                predicted_views_7d=predicted_7d,
                growth_pattern=pattern,
                is_darkhorse=is_darkhorse,
            )
            db.add(analysis)

    db.commit()


def collect_stats_for_videos(db: Session, video_ids: list[str]):
    """특정 영상들의 통계를 즉시 수집하고 분석"""
    if not video_ids:
        return
    stats_list = fetch_video_stats(video_ids)
    _save_stats_and_analyze(db, stats_list)


def _update_subscriber_counts(db: Session):
    """구독자 수가 0인 영상의 채널 구독자 수를 업데이트"""
    from app.crawler.youtube_search import _fetch_subscriber_counts

    videos = db.query(Video).filter(
        (Video.subscriber_count == 0) | (Video.subscriber_count.is_(None))
    ).all()
    if not videos:
        return

    channel_ids = list(set(v.channel_id for v in videos if v.channel_id))
    if not channel_ids:
        return

    sub_counts = _fetch_subscriber_counts(channel_ids)
    for video in videos:
        count = sub_counts.get(video.channel_id, 0)
        if count > 0:
            video.subscriber_count = count
    db.commit()


def collect_and_analyze(db: Session, tier: str = "recent"):
    """영상 통계를 수집하고 VPH/점수 계산 (티어별 차등 수집)

    tier:
      "recent"  → 최근 7일 영상 (30분마다)
      "mid"     → 7~30일 영상 (2시간마다)
      "old"     → 30일+ 영상 (하루 1회)
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)

    if tier == "recent":
        cutoff = now - timedelta(days=7)
        video_ids = [
            v[0] for v in db.query(Video.video_id)
            .filter(Video.published_at >= cutoff)
            .all()
        ]
    elif tier == "mid":
        recent_cutoff = now - timedelta(days=7)
        old_cutoff = now - timedelta(days=30)
        video_ids = [
            v[0] for v in db.query(Video.video_id)
            .filter(Video.published_at >= old_cutoff, Video.published_at < recent_cutoff)
            .all()
        ]
    else:  # old
        old_cutoff = now - timedelta(days=30)
        video_ids = [
            v[0] for v in db.query(Video.video_id)
            .filter(Video.published_at < old_cutoff)
            .all()
        ]

    if not video_ids:
        return

    logger.info("collect_and_analyze [%s]: %d videos", tier, len(video_ids))

    stats_list = fetch_video_stats(video_ids)
    _save_stats_and_analyze(db, stats_list)

    # 구독자 수 업데이트 (recent 티어에서만, 중복 방지)
    if tier == "recent":
        _update_subscriber_counts(db)
