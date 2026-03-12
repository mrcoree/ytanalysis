from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, desc
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from app.db.database import get_db
from app.db.models import Video, VideoStats, Analysis, SearchHistory, VideoKeyword, User
from app.api.shared import build_video_response, batch_latest_stats, get_blacklisted_channel_ids, duration_to_seconds, get_current_user, sanitize_csv_field
import csv
import html
import io

router = APIRouter()


def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return current_user


def _get_user_video_ids(db: Session, user_id: int) -> list[str]:
    """사용자가 검색한 키워드에 연결된 영상 ID 목록"""
    user_kws = [k[0] for k in db.query(SearchHistory.keyword).filter(
        SearchHistory.user_id == user_id).distinct().all()]
    if not user_kws:
        return []
    return [v[0] for v in db.query(VideoKeyword.video_id).filter(
        VideoKeyword.keyword.in_(user_kws)).distinct().all()]


@router.get("/video/{video_id}/stats")
def get_video_stats(video_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """영상의 조회수 이력 반환"""
    video = db.query(Video).filter(Video.video_id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    stats = (
        db.query(VideoStats)
        .filter(VideoStats.video_id == video_id)
        .order_by(VideoStats.collected_at.desc())
        .limit(1500)
        .all()
    )

    return {
        "video_id": video_id,
        "title": html.unescape(video.title or ""),
        "stats": [
            {
                "views": s.views,
                "likes": s.likes,
                "comments": s.comments,
                "collected_at": s.collected_at,
            }
            for s in stats
        ],
    }


def _filter_by_duration_python(items, duration: str | None, video_extractor=None):
    """Python 레벨에서 duration 필터 적용. items는 (Video, Analysis) 튜플 리스트."""
    if not duration:
        return items
    result = []
    for item in items:
        v = video_extractor(item) if video_extractor else item
        secs = duration_to_seconds(v.duration or "")
        if duration == "short" and secs <= 180:
            result.append(item)
        elif duration == "medium" and 180 < secs <= 1200:
            result.append(item)
        elif duration == "long" and secs > 1200:
            result.append(item)
    return result


@router.get("/dashboard")
def get_dashboard(
    keyword: str = Query(None, description="특정 키워드로 필터링"),
    duration: str = Query(None, description="영상 길이 필터: short, medium, long"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """대시보드 통계 (사용자 검색 영상만)"""
    _my_vids = _get_user_video_ids(db, current_user.id)
    total_videos = len(_my_vids)
    total_stats = db.query(func.count(VideoStats.id)).filter(
        VideoStats.video_id.in_(_my_vids)).scalar() if _my_vids else 0

    # 최근 검색 키워드 TOP 5 (본인 검색만)
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_keywords = (
        db.query(SearchHistory.keyword, func.count(SearchHistory.id).label("cnt"))
        .filter(SearchHistory.searched_at >= cutoff_24h, SearchHistory.user_id == current_user.id)
        .group_by(SearchHistory.keyword)
        .order_by(desc("cnt"))
        .limit(5)
        .all()
    )

    blacklisted = get_blacklisted_channel_ids(db, current_user.id)

    def apply_filters(query):
        if keyword:
            safe_kw = keyword.replace("%", "\\%").replace("_", "\\_")
            query = query.filter(Video.title.ilike(f"%{safe_kw}%", escape="\\"))
        elif _my_vids:
            query = query.filter(Video.video_id.in_(_my_vids))
        if blacklisted:
            query = query.filter(Video.channel_id.notin_(blacklisted))
        return query

    # 떡상 TOP 5
    top_query = (
        db.query(Video, Analysis)
        .join(Analysis, Video.video_id == Analysis.video_id)
        .filter(Analysis.vph > 0)
    )
    top_query = apply_filters(top_query)
    # duration 필터가 있으면 넉넉히 가져와서 Python에서 필터
    fetch_limit = 50 if duration else 5
    top_viral_raw = top_query.order_by(desc(Analysis.vph)).limit(fetch_limit).all()
    top_viral = _filter_by_duration_python(top_viral_raw, duration, lambda x: x[0])[:5]

    # 배치로 최신 통계 조회
    top_video_ids = [video.video_id for video, analysis in top_viral]
    stats_map = batch_latest_stats(db, top_video_ids)

    top_viral_list = []
    for video, analysis in top_viral:
        latest = stats_map.get(video.video_id)
        top_viral_list.append({
            "video_id": video.video_id,
            "title": html.unescape(video.title or ""),
            "channel_id": video.channel_id,
            "channel_title": video.channel_title or "",
            "thumbnail": video.thumbnail,
            "vph": analysis.vph,
            "score": analysis.score,
            "views": latest.views if latest else 0,
            "growth_pattern": analysis.growth_pattern,
            "is_darkhorse": analysis.is_darkhorse,
        })

    # 다크호스 영상 TOP 5
    dh_query = (
        db.query(Video, Analysis)
        .join(Analysis, Video.video_id == Analysis.video_id)
        .filter(Analysis.is_darkhorse.is_(True))
    )
    dh_query = apply_filters(dh_query)
    dh_fetch_limit = 50 if duration else 5
    darkhorses_raw = dh_query.order_by(desc(Analysis.vph)).limit(dh_fetch_limit).all()
    darkhorses = _filter_by_duration_python(darkhorses_raw, duration, lambda x: x[0])[:5]

    dh_video_ids = [v.video_id for v, a in darkhorses]
    dh_stats_map = batch_latest_stats(db, dh_video_ids) if dh_video_ids else {}
    darkhorse_list = [{
        "video_id": v.video_id,
        "title": html.unescape(v.title or ""),
        "channel_id": v.channel_id,
        "channel_title": v.channel_title or "",
        "thumbnail": v.thumbnail,
        "vph": a.vph,
        "score": a.score,
        "views": dh_stats_map[v.video_id].views if v.video_id in dh_stats_map else 0,
        "growth_pattern": a.growth_pattern,
    } for v, a in darkhorses]

    # 최근 수집 시각
    latest_collection = db.query(func.max(VideoStats.collected_at)).scalar()

    return {
        "total_videos": total_videos,
        "total_stats_records": total_stats,
        "top_viral": top_viral_list,
        "darkhorses": darkhorse_list,
        "recent_keywords": [{"keyword": r.keyword, "count": r.cnt} for r in recent_keywords],
        "last_collected_at": latest_collection,
    }


@router.get("/channel/{channel_id}/videos")
def get_channel_videos(channel_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """채널별 영상 목록 + 평균 VPH/스코어"""
    videos = db.query(Video).filter(Video.channel_id == channel_id).all()
    if not videos:
        raise HTTPException(status_code=404, detail="Channel not found")

    video_ids = [v.video_id for v in videos]

    # 배치로 최신 통계 + 분석 조회
    stats_map = batch_latest_stats(db, video_ids)
    analyses = db.query(Analysis).filter(Analysis.video_id.in_(video_ids)).all()
    analysis_map = {a.video_id: a for a in analyses}

    result = []
    total_vph = 0
    total_score = 0
    count = 0
    for video in videos:
        analysis = analysis_map.get(video.video_id)
        latest = stats_map.get(video.video_id)
        vph = analysis.vph if analysis else 0
        score = analysis.score if analysis else 0
        total_vph += vph
        total_score += score
        count += 1
        result.append({
            "video_id": video.video_id,
            "title": html.unescape(video.title or ""),
            "thumbnail": video.thumbnail,
            "published_at": video.published_at,
            "views": latest.views if latest else 0,
            "likes": latest.likes if latest else 0,
            "vph": vph,
            "score": score,
        })

    result.sort(key=lambda x: x["score"], reverse=True)

    return {
        "channel_id": channel_id,
        "channel_title": videos[0].channel_title if videos else "",
        "video_count": count,
        "avg_vph": round(total_vph / count, 2) if count else 0,
        "avg_score": round(total_score / count, 2) if count else 0,
        "videos": result,
    }


@router.get("/export/csv")
def export_csv(
    keyword: str = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """검색 결과를 CSV로 내보내기 (사용자 검색 영상만)"""
    my_vids = _get_user_video_ids(db, current_user.id)
    query = (
        db.query(Video, Analysis)
        .outerjoin(Analysis, Video.video_id == Analysis.video_id)
    )

    if keyword:
        safe_kw = keyword.replace("%", "\\%").replace("_", "\\_")
        query = query.filter(Video.title.ilike(f"%{safe_kw}%", escape="\\"))
    elif my_vids:
        query = query.filter(Video.video_id.in_(my_vids))

    query = query.order_by(desc(Analysis.score))
    rows = query.limit(500).all()

    # 배치로 최신 통계 조회
    video_ids = [video.video_id for video, analysis in rows]
    stats_map = batch_latest_stats(db, video_ids)

    output = io.StringIO()
    output.write('\ufeff')  # BOM for Excel
    writer = csv.writer(output)
    writer.writerow([
        "video_id", "title", "channel_title", "published_at",
        "views", "likes", "comments", "vph", "score", "youtube_url"
    ])

    for video, analysis in rows:
        latest = stats_map.get(video.video_id)
        writer.writerow([
            sanitize_csv_field(video.video_id),
            sanitize_csv_field(video.title),
            sanitize_csv_field(video.channel_title or ""),
            video.published_at.isoformat() if video.published_at else "",
            latest.views if latest else 0,
            latest.likes if latest else 0,
            latest.comments if latest else 0,
            analysis.vph if analysis else 0,
            analysis.score if analysis else 0,
            f"https://www.youtube.com/watch?v={video.video_id}",
        ])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=viral_radar_export.csv"},
    )


@router.get("/title-patterns")
def get_title_patterns(db: Session = Depends(get_db), current_user: User = Depends(_require_admin)):
    """떡상 영상 제목 패턴 분석 (사용자 검색 영상만)"""
    import re
    from collections import Counter

    my_vids = _get_user_video_ids(db, current_user.id)
    query = (
        db.query(Video.title, Analysis.vph)
        .join(Analysis, Video.video_id == Analysis.video_id)
        .filter(Analysis.vph > 0)
    )
    if my_vids:
        query = query.filter(Video.video_id.in_(my_vids))
    rows = query.order_by(desc(Analysis.vph)).limit(200).all()

    if not rows:
        return {"patterns": [], "top_bigrams": [], "total_analyzed": 0}

    # 불용어 (한국어 + 영어 일반 단어)
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "for",
        "of", "and", "or", "but", "with", "this", "that", "it", "be", "as", "by",
        "이", "그", "저", "것", "수", "등", "들", "및", "에", "의", "가", "을", "를",
        "은", "는", "로", "으로", "와", "과", "도", "에서", "까지", "한", "하는",
        "된", "할", "하고", "하면", "합니다", "있는", "없는", "하는", "되는",
    }

    word_counter = Counter()
    bigram_counter = Counter()
    bracket_counter = Counter()  # [키워드], 【키워드】 패턴

    for title, vph in rows:
        # 대괄호/꺾쇠 안의 키워드 추출
        brackets = re.findall(r'[\[【\(](.*?)[\]】\)]', title)
        for b in brackets:
            b = b.strip()
            if b and len(b) <= 20:
                bracket_counter[b] += 1

        # 단어 분리 (한글, 영문, 숫자)
        words = re.findall(r'[가-힣]+|[a-zA-Z]+', title.lower())
        words = [w for w in words if len(w) >= 2 and w not in stopwords]

        for w in words:
            word_counter[w] += 1

        # 바이그램
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i+1]}"
            bigram_counter[bigram] += 1

    # 상위 30개 단어
    patterns = [{"word": w, "count": c} for w, c in word_counter.most_common(30)]
    # 상위 15개 바이그램
    top_bigrams = [{"phrase": b, "count": c} for b, c in bigram_counter.most_common(15)]
    # 상위 10개 괄호 키워드
    top_brackets = [{"keyword": k, "count": c} for k, c in bracket_counter.most_common(10)]

    return {
        "patterns": patterns,
        "top_bigrams": top_bigrams,
        "top_brackets": top_brackets,
        "total_analyzed": len(rows),
    }


@router.get("/upload-time-analysis")
def get_upload_time_analysis(db: Session = Depends(get_db), current_user: User = Depends(_require_admin)):
    """최적 업로드 시간 분석 - 요일/시간대별 평균 VPH (사용자 검색 영상만)"""
    my_vids = _get_user_video_ids(db, current_user.id)
    query = (
        db.query(Video.published_at, Analysis.vph)
        .join(Analysis, Video.video_id == Analysis.video_id)
        .filter(Video.published_at.isnot(None), Analysis.vph > 0)
    )
    if my_vids:
        query = query.filter(Video.video_id.in_(my_vids))
    rows = query.all()

    if not rows:
        return {"heatmap": [], "best_times": []}

    from collections import defaultdict
    # {(weekday, hour): [vph_list]}
    time_slots = defaultdict(list)
    for published_at, vph in rows:
        # Convert to KST (UTC+9)
        kst = published_at + timedelta(hours=9)
        weekday = kst.weekday()  # 0=Monday
        hour = kst.hour
        time_slots[(weekday, hour)].append(vph)

    # Build heatmap data
    day_names = ["월", "화", "수", "목", "금", "토", "일"]
    heatmap = []
    for weekday in range(7):
        for hour in range(24):
            vphs = time_slots.get((weekday, hour), [])
            avg_vph = round(sum(vphs) / len(vphs), 1) if vphs else 0
            heatmap.append({
                "day": weekday,
                "day_name": day_names[weekday],
                "hour": hour,
                "avg_vph": avg_vph,
                "count": len(vphs),
            })

    # Top 5 best time slots
    best = sorted(heatmap, key=lambda x: x["avg_vph"], reverse=True)[:5]

    return {
        "heatmap": heatmap,
        "best_times": [{"day_name": b["day_name"], "hour": b["hour"], "avg_vph": b["avg_vph"], "count": b["count"]} for b in best],
        "total_analyzed": len(rows),
    }


@router.get("/blue-ocean-keywords")
def get_blue_ocean_keywords(db: Session = Depends(get_db), current_user: User = Depends(_require_admin)):
    """블루오션 키워드 발굴 — VPH 높은데 영상 수 적은 키워드"""
    my_vids = _get_user_video_ids(db, current_user.id)
    if not my_vids:
        return {"keywords": []}

    # 사용자의 키워드별 영상 수, 평균 VPH, 평균 조회수
    user_kws = [k[0] for k in db.query(SearchHistory.keyword).filter(
        SearchHistory.user_id == current_user.id).distinct().all()]
    if not user_kws:
        return {"keywords": []}

    results = []
    for kw in user_kws:
        vid_ids = [v[0] for v in db.query(VideoKeyword.video_id).filter(
            VideoKeyword.keyword == kw).all()]
        if not vid_ids:
            continue
        analyses = db.query(Analysis).filter(
            Analysis.video_id.in_(vid_ids), Analysis.vph > 0).all()
        if not analyses:
            continue
        avg_vph = sum(a.vph for a in analyses) / len(analyses)
        video_count = len(vid_ids)
        avg_score = sum(a.score for a in analyses) / len(analyses)

        results.append({
            "keyword": kw,
            "video_count": video_count,
            "avg_vph": round(avg_vph, 1),
            "avg_score": round(avg_score, 1),
        })

    # 블루오션 점수: VPH 높고 영상 수 적을수록 높음
    if results:
        max_vph = max(r["avg_vph"] for r in results) or 1
        max_count = max(r["video_count"] for r in results) or 1
        for r in results:
            vph_norm = r["avg_vph"] / max_vph
            count_norm = 1 - (r["video_count"] / max_count)  # 영상 적을수록 높음
            r["blue_ocean_score"] = round(vph_norm * 0.6 + count_norm * 0.4, 3)
        results.sort(key=lambda x: x["blue_ocean_score"], reverse=True)

    return {"keywords": results[:20]}


@router.get("/trend-detection")
def get_trend_detection(db: Session = Depends(get_db), current_user: User = Depends(_require_admin)):
    """트렌드 감지 — 최근 VPH가 급상승한 키워드"""
    my_vids = _get_user_video_ids(db, current_user.id)
    if not my_vids:
        return {"trends": []}

    user_kws = [k[0] for k in db.query(SearchHistory.keyword).filter(
        SearchHistory.user_id == current_user.id).distinct().all()]
    if not user_kws:
        return {"trends": []}

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=2)
    older_cutoff = now - timedelta(days=7)

    results = []
    for kw in user_kws:
        vid_ids = [v[0] for v in db.query(VideoKeyword.video_id).filter(
            VideoKeyword.keyword == kw).all()]
        if not vid_ids:
            continue

        # 최근 2일간 평균 VPH
        recent_stats = (
            db.query(func.avg(Analysis.vph))
            .join(Video, Video.video_id == Analysis.video_id)
            .filter(Analysis.video_id.in_(vid_ids), Analysis.vph > 0,
                    Video.published_at >= recent_cutoff)
            .scalar()
        ) or 0

        # 7일 전체 평균 VPH
        older_stats = (
            db.query(func.avg(Analysis.vph))
            .filter(Analysis.video_id.in_(vid_ids), Analysis.vph > 0)
            .scalar()
        ) or 0

        if older_stats > 0 and recent_stats > 0:
            change_pct = ((recent_stats - older_stats) / older_stats) * 100
            results.append({
                "keyword": kw,
                "recent_avg_vph": round(recent_stats, 1),
                "overall_avg_vph": round(older_stats, 1),
                "change_pct": round(change_pct, 1),
                "video_count": len(vid_ids),
            })

    results.sort(key=lambda x: x["change_pct"], reverse=True)
    return {"trends": results[:20]}


@router.get("/optimal-duration")
def get_optimal_duration(db: Session = Depends(get_db), current_user: User = Depends(_require_admin)):
    """최적 영상 길이 분석 — 길이 구간별 평균 VPH/조회수"""
    from app.api.shared import duration_to_seconds as d2s
    my_vids = _get_user_video_ids(db, current_user.id)
    query = (
        db.query(Video.duration, Analysis.vph, Analysis.score)
        .join(Analysis, Video.video_id == Analysis.video_id)
        .filter(Video.duration.isnot(None), Video.duration != "", Analysis.vph > 0)
    )
    if my_vids:
        query = query.filter(Video.video_id.in_(my_vids))
    rows = query.all()

    if not rows:
        return {"buckets": []}

    from collections import defaultdict
    buckets = defaultdict(lambda: {"vphs": [], "scores": [], "count": 0})
    bucket_labels = [
        (0, 60, "~1분"),
        (60, 180, "1~3분"),
        (180, 600, "3~10분"),
        (600, 1200, "10~20분"),
        (1200, 3600, "20~60분"),
        (3600, float("inf"), "60분+"),
    ]

    for duration, vph, score in rows:
        secs = d2s(duration)
        if secs <= 0:
            continue
        for low, high, label in bucket_labels:
            if low < secs <= high:
                buckets[label]["vphs"].append(vph)
                buckets[label]["scores"].append(score)
                buckets[label]["count"] += 1
                break

    result = []
    for low, high, label in bucket_labels:
        b = buckets.get(label)
        if not b or not b["vphs"]:
            continue
        result.append({
            "label": label,
            "count": b["count"],
            "avg_vph": round(sum(b["vphs"]) / len(b["vphs"]), 1),
            "avg_score": round(sum(b["scores"]) / len(b["scores"]), 1),
        })

    return {"buckets": result}


@router.get("/small-channel-viral")
def get_small_channel_viral(db: Session = Depends(get_db), current_user: User = Depends(_require_admin)):
    """소형 채널 떡상 감지 — 구독자 대비 조회수가 폭발적인 영상"""
    my_vids = _get_user_video_ids(db, current_user.id)
    query = (
        db.query(Video, Analysis)
        .join(Analysis, Video.video_id == Analysis.video_id)
        .filter(
            Video.subscriber_count > 0,
            Video.subscriber_count <= 10000,
            Analysis.vph > 0,
        )
    )
    if my_vids:
        query = query.filter(Video.video_id.in_(my_vids))
    rows = query.order_by(desc(Analysis.vph)).limit(50).all()

    if not rows:
        return {"videos": []}

    video_ids = [v.video_id for v, a in rows]
    stats_map = batch_latest_stats(db, video_ids)

    result = []
    for video, analysis in rows:
        latest = stats_map.get(video.video_id)
        views = latest.views if latest else 0
        if views == 0:
            continue
        # 구독자 대비 조회수 비율
        view_sub_ratio = views / video.subscriber_count if video.subscriber_count else 0
        result.append({
            "video_id": video.video_id,
            "title": html.unescape(video.title or ""),
            "channel_title": video.channel_title or "",
            "channel_id": video.channel_id,
            "thumbnail": video.thumbnail,
            "subscriber_count": video.subscriber_count,
            "views": views,
            "vph": analysis.vph,
            "score": analysis.score,
            "view_sub_ratio": round(view_sub_ratio, 1),
            "growth_pattern": analysis.growth_pattern,
        })

    result.sort(key=lambda x: x["view_sub_ratio"], reverse=True)
    return {"videos": result[:20]}


@router.get("/engagement-analysis")
def get_engagement_analysis(db: Session = Depends(get_db), current_user: User = Depends(_require_admin)):
    """참여율 분석 — 좋아요/댓글 비율이 높은 영상의 특징"""
    my_vids = _get_user_video_ids(db, current_user.id)
    if not my_vids:
        return {"videos": [], "avg_like_rate": 0, "avg_comment_rate": 0}

    video_ids = my_vids[:500]
    stats_map = batch_latest_stats(db, video_ids)
    analyses = db.query(Analysis).filter(Analysis.video_id.in_(video_ids)).all()
    analysis_map = {a.video_id: a for a in analyses}
    videos = db.query(Video).filter(Video.video_id.in_(video_ids)).all()
    video_map = {v.video_id: v for v in videos}

    entries = []
    for vid in video_ids:
        stat = stats_map.get(vid)
        if not stat or stat.views < 100:
            continue
        video = video_map.get(vid)
        analysis = analysis_map.get(vid)
        if not video:
            continue
        like_rate = (stat.likes / stat.views * 100) if stat.views else 0
        comment_rate = (stat.comments / stat.views * 100) if stat.views else 0
        entries.append({
            "video_id": vid,
            "title": html.unescape(video.title or ""),
            "channel_title": video.channel_title or "",
            "thumbnail": video.thumbnail,
            "views": stat.views,
            "likes": stat.likes,
            "comments": stat.comments,
            "like_rate": round(like_rate, 2),
            "comment_rate": round(comment_rate, 3),
            "vph": analysis.vph if analysis else 0,
            "engagement_score": round(like_rate * 0.7 + comment_rate * 100 * 0.3, 1),
        })

    entries.sort(key=lambda x: x["engagement_score"], reverse=True)
    avg_like = sum(e["like_rate"] for e in entries) / len(entries) if entries else 0
    avg_comment = sum(e["comment_rate"] for e in entries) / len(entries) if entries else 0

    return {
        "videos": entries[:20],
        "avg_like_rate": round(avg_like, 2),
        "avg_comment_rate": round(avg_comment, 3),
    }


@router.get("/channel/{channel_id}/vph-growth")
def get_channel_vph_growth(channel_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """채널 평균 VPH 성장 곡선 vs 개별 영상 VPH 성장 곡선

    각 영상의 '게시 후 경과 시간별 VPH'를 계산하여
    채널 평균 기준선과 비교할 수 있는 데이터를 반환한다.
    """
    videos = db.query(Video).filter(
        Video.channel_id == channel_id,
        Video.published_at.isnot(None),
    ).all()

    if not videos:
        raise HTTPException(status_code=404, detail="Channel not found")

    video_ids = [v.video_id for v in videos]
    video_map = {v.video_id: v for v in videos}

    # 모든 영상의 스탯을 한 번에 조회
    all_stats = (
        db.query(VideoStats)
        .filter(VideoStats.video_id.in_(video_ids))
        .order_by(VideoStats.collected_at.asc())
        .all()
    )

    # 영상별로 그룹핑
    from collections import defaultdict
    stats_by_video = defaultdict(list)
    for s in all_stats:
        stats_by_video[s.video_id].append(s)

    # 각 영상의 '게시 후 경과 시간(h) → VPH' 곡선 계산
    video_curves = []
    # 채널 평균용: 경과시간 → VPH 리스트
    avg_bucket = defaultdict(list)

    for vid, stats in stats_by_video.items():
        video = video_map.get(vid)
        if not video or not video.published_at or len(stats) < 2:
            continue

        published = video.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)

        curve_points = []
        for i in range(1, len(stats)):
            prev = stats[i - 1]
            curr = stats[i]
            time_diff_h = (curr.collected_at - prev.collected_at).total_seconds() / 3600
            if time_diff_h <= 0:
                continue
            vph = max((curr.views - prev.views) / time_diff_h, 0)

            # 게시 후 경과 시간 (중간점 기준)
            mid_time = prev.collected_at + (curr.collected_at - prev.collected_at) / 2
            hours_since = (mid_time - published).total_seconds() / 3600
            if hours_since < 0:
                continue

            # 6시간 단위 버킷 (0-6h, 6-12h, ...)
            bucket = int(hours_since // 6) * 6
            if bucket > 720:  # 30일까지
                continue

            curve_points.append({"hours": bucket, "vph": round(vph, 1)})
            avg_bucket[bucket].append(vph)

        if curve_points:
            # 같은 버킷의 VPH를 평균
            bucket_map = defaultdict(list)
            for p in curve_points:
                bucket_map[p["hours"]].append(p["vph"])
            averaged = [{"hours": h, "vph": round(sum(vs) / len(vs), 1)}
                        for h, vs in sorted(bucket_map.items())]

            analysis = db.query(Analysis).filter(Analysis.video_id == vid).first()
            video_curves.append({
                "video_id": vid,
                "title": html.unescape(video.title or ""),
                "current_vph": analysis.vph if analysis else 0,
                "is_above_avg": False,  # 아래에서 계산
                "points": averaged,
            })

    # 채널 평균 곡선
    avg_curve = []
    for h in sorted(avg_bucket.keys()):
        vphs = avg_bucket[h]
        avg_curve.append({"hours": h, "vph": round(sum(vphs) / len(vphs), 1)})

    # 평균 VPH 계산
    channel_avg_vph = sum(p["vph"] for p in avg_curve) / len(avg_curve) if avg_curve else 0

    # 각 영상이 평균 대비 떡상인지 판단
    for vc in video_curves:
        vid_avg = sum(p["vph"] for p in vc["points"]) / len(vc["points"]) if vc["points"] else 0
        vc["is_above_avg"] = vid_avg > channel_avg_vph * 1.3  # 30% 이상이면 떡상

    # VPH 높은 순 정렬, 상위 5개만
    video_curves.sort(key=lambda x: x["current_vph"], reverse=True)
    top_curves = video_curves[:5]

    return {
        "channel_id": channel_id,
        "channel_title": videos[0].channel_title if videos else "",
        "avg_curve": avg_curve,
        "channel_avg_vph": round(channel_avg_vph, 1),
        "video_curves": top_curves,
        "total_videos": len(videos),
    }
