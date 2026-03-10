from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.db.models import VideoStats, Video


def calculate_vph(db: Session, video_id: str) -> float:
    """VPH(시간당 조회수 증가량) 계산

    - 스냅샷 2개 이상: 최근 두 스냅샷 간 시간당 조회수 변화
    - 스냅샷 1개: 총 조회수 / 게시 후 경과 시간 (초기 VPH)

    VPH는 총 조회수를 초과할 수 없다.
    최소 수집 간격 10분 미만이면 신뢰할 수 없으므로 게시일 기준으로 대체.
    """
    stats = (
        db.query(VideoStats)
        .filter(VideoStats.video_id == video_id)
        .order_by(VideoStats.collected_at.desc())
        .limit(2)
        .all()
    )

    current_views = stats[0].views if stats else 0

    if len(stats) >= 2:
        current = stats[0]
        previous = stats[1]
        time_diff_h = (current.collected_at - previous.collected_at).total_seconds() / 3600

        # 수집 간격이 10분 이상이어야 신뢰할 수 있는 VPH
        if time_diff_h >= 1/6:
            view_diff = current.views - previous.views
            vph = max(view_diff / time_diff_h, 0)
            # VPH가 총 조회수보다 클 수 없음
            return round(min(vph, current.views), 2)

    # 스냅샷 부족 또는 간격 너무 짧음 → 게시일 기준 평균 VPH
    if stats:
        video = db.query(Video).filter(Video.video_id == video_id).first()
        if video and video.published_at and current_views > 0:
            published = video.published_at
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            hours_since = (datetime.now(timezone.utc) - published).total_seconds() / 3600
            if hours_since >= 1:
                return round(current_views / hours_since, 2)

    return 0.0
