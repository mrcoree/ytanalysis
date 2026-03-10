from sqlalchemy.orm import Session
from app.db.models import VideoStats


def predict_views(db: Session, video_id: str, current_views: int, current_vph: float) -> tuple[int, int]:
    """조회수 예측 (24시간 후, 7일 후)

    3개 이상 스냅샷이 있으면 VPH 가속도를 반영하여 예측.
    스냅샷이 부족하면 현재 VPH를 단순 적용.

    Returns:
        (predicted_views_24h, predicted_views_7d)
    """
    if current_vph <= 0:
        return current_views, current_views

    # VPH가 조회수보다 크면 비정상 → 보정
    current_vph = min(current_vph, current_views) if current_views > 0 else current_vph

    stats = (
        db.query(VideoStats)
        .filter(VideoStats.video_id == video_id)
        .order_by(VideoStats.collected_at.desc())
        .limit(5)
        .all()
    )

    if len(stats) >= 3:
        # VPH 가속도 계산: 최근 VPH vs 이전 VPH 비교
        recent_vph = _calc_vph_between(stats[0], stats[1])
        older_vph = _calc_vph_between(stats[1], stats[2])

        if older_vph > 0:
            acceleration = recent_vph / older_vph  # >1이면 가속, <1이면 감속
            acceleration = max(0.3, min(acceleration, 3.0))  # 클램프
        else:
            acceleration = 1.0

        # 가속도를 반영한 예측
        # 24시간: 현재 VPH에 가속도 감쇠 적용
        adjusted_vph_24h = current_vph * (acceleration ** 0.5)  # 루트로 감쇠
        predicted_24h = current_views + int(adjusted_vph_24h * 24)

        # 7일: 장기 예측은 가속도를 더 보수적으로
        adjusted_vph_7d = current_vph * (acceleration ** 0.3)
        predicted_7d = current_views + int(adjusted_vph_7d * 168)
    else:
        # 단순 선형 예측
        predicted_24h = current_views + int(current_vph * 24)
        predicted_7d = current_views + int(current_vph * 168)

    return max(predicted_24h, current_views), max(predicted_7d, current_views)


def _calc_vph_between(newer: VideoStats, older: VideoStats) -> float:
    """두 스냅샷 사이의 VPH 계산"""
    time_diff = (newer.collected_at - older.collected_at).total_seconds() / 3600
    if time_diff < 1/6:  # 10분 미만이면 신뢰 불가
        return 0.0
    view_diff = newer.views - older.views
    return max(view_diff / time_diff, 0.0)
