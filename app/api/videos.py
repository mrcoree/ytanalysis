from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Video, Analysis, SearchHistory, VideoStats, User
from app.crawler.youtube_search import discover_and_store
from app.api.shared import build_video_response, batch_latest_stats, get_blacklisted_channel_ids, filter_by_duration, filter_by_period, get_current_user

router = APIRouter()


@router.get("/tags/recent")
def get_recent_tags(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    rows = (
        db.query(SearchHistory.keyword, func.count(SearchHistory.id).label("cnt"))
        .filter(SearchHistory.searched_at >= cutoff, SearchHistory.user_id == current_user.id)
        .group_by(SearchHistory.keyword)
        .order_by(func.count(SearchHistory.id).desc())
        .limit(10)
        .all()
    )
    return {"tags": [row.keyword for row in rows]}


@router.delete("/tags/{keyword}")
def delete_tag(keyword: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    deleted = db.query(SearchHistory).filter(SearchHistory.keyword == keyword, SearchHistory.user_id == current_user.id).delete()
    db.commit()
    return {"ok": True, "deleted": deleted}


@router.get("/videos")
def get_videos(
    keyword: str = Query(..., description="검색 키워드"),
    duration: str | None = Query(None, description="영상 길이 필터: short, medium, long"),
    period: str | None = Query(None, description="업로드 기간 필터: 1d, 1w, 2w, 1m, 3m, 6m"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 0. 키워드 유효성 검사 & 검색 기록 저장
    keyword = keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="검색 키워드를 입력하세요")
    db.add(SearchHistory(keyword=keyword, user_id=current_user.id))
    db.commit()

    # 1. 영상 검색 & DB 저장 (API 실패 시 DB 캐시 사용)
    videos = discover_and_store(db, keyword, duration=duration, period=period)
    if not videos:
        from app.db.models import VideoKeyword
        cached_ids = [vk.video_id for vk in
            db.query(VideoKeyword.video_id).filter(VideoKeyword.keyword == keyword).all()]
        if not cached_ids:
            safe_kw = keyword.replace("%", "\\%").replace("_", "\\_")
            cached_videos = db.query(Video).filter(Video.title.ilike(f"%{safe_kw}%", escape="\\")).limit(20).all()
            cached_ids = [v.video_id for v in cached_videos]
        if not cached_ids:
            return {"videos": [], "cached": False}
        video_ids = cached_ids
    else:
        video_ids = [v["video_id"] for v in videos]

    # 2. 배치로 최신 통계 + 분석 조회 (통계는 discover_and_store에서 이미 저장됨)
    stats_map = batch_latest_stats(db, video_ids)
    analyses = db.query(Analysis).filter(Analysis.video_id.in_(video_ids)).all()
    analysis_map = {a.video_id: a for a in analyses}

    # 4. 결과 조합 (블랙리스트 필터 적용)
    blacklisted = get_blacklisted_channel_ids(db, current_user.id)
    db_videos = db.query(Video).filter(Video.video_id.in_(video_ids)).all()
    video_model_map = {v.video_id: v for v in db_videos}

    result = []
    for vid in video_ids:
        video_model = video_model_map.get(vid)
        if not video_model:
            continue
        if video_model.channel_id in blacklisted:
            continue
        result.append(build_video_response(
            video_model,
            analysis=analysis_map.get(vid),
            latest_stat=stats_map.get(vid),
        ))

    # 5. duration + period 필터 적용 (캐시 결과에도 적용)
    result = filter_by_duration(result, duration)
    result = filter_by_period(result, period)

    # 6. 떡상 점수 내림차순 정렬
    result.sort(key=lambda x: x["score"], reverse=True)

    return {"videos": result}


@router.get("/video/{video_id}")
def get_video(video_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    video = db.query(Video).filter(Video.video_id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    analysis = db.query(Analysis).filter(Analysis.video_id == video_id).first()
    latest_stat = (
        db.query(VideoStats)
        .filter(VideoStats.video_id == video_id)
        .order_by(VideoStats.collected_at.desc())
        .first()
    )

    return build_video_response(video, analysis=analysis, latest_stat=latest_stat)
