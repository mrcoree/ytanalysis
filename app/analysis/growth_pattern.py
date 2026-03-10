from sqlalchemy.orm import Session
from app.db.models import VideoStats


def classify_growth_pattern(db: Session, video_id: str) -> str:
    """성장 패턴 분류

    Returns:
        "explosive" - 급상승 (VPH가 계속 증가)
        "steady" - 꾸준한 증가 (VPH가 안정적)
        "plateau" - 정체 (VPH가 낮고 변화 없음)
        "declining" - 하락 (VPH가 감소 추세)
        "unknown" - 데이터 부족
    """
    stats = (
        db.query(VideoStats)
        .filter(VideoStats.video_id == video_id)
        .order_by(VideoStats.collected_at.asc())
        .all()
    )

    if len(stats) < 3:
        return "unknown"

    # 구간별 VPH 계산
    vphs = []
    for i in range(1, len(stats)):
        time_diff = (stats[i].collected_at - stats[i - 1].collected_at).total_seconds() / 3600
        if time_diff > 0:
            vph = (stats[i].views - stats[i - 1].views) / time_diff
            vphs.append(max(vph, 0.0))

    if not vphs:
        return "unknown"

    avg_vph = sum(vphs) / len(vphs)

    # 최근 절반 vs 이전 절반 비교
    mid = len(vphs) // 2
    if mid == 0:
        return "unknown"

    early_avg = sum(vphs[:mid]) / mid
    recent_avg = sum(vphs[mid:]) / len(vphs[mid:])

    if early_avg <= 0:
        if recent_avg > 10:
            return "explosive"
        return "unknown"

    ratio = recent_avg / early_avg

    if ratio > 1.5 and recent_avg > 50:
        return "explosive"
    elif ratio > 0.8:
        if avg_vph > 10:
            return "steady"
        else:
            return "plateau"
    else:
        return "declining"
