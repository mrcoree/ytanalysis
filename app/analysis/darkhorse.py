from sqlalchemy import func
from sqlalchemy.orm import Session
from app.db.models import Video, Analysis


def detect_darkhorse(db: Session, video_id: str, current_vph: float, avg_vph: float = None) -> bool:
    """다크호스 감지: 소규모 채널에서 폭발적 조회수를 기록하는 영상

    조건 (구독자 수 있을 때):
    1. 구독자 10만 이하
    2. VPH가 전체 평균의 2배 이상

    조건 (구독자 수 없을 때 - 기존 방식):
    1. 채널의 트래킹 영상 수 3개 이하
    2. VPH가 전체 평균의 2배 이상
    """
    if current_vph <= 0:
        return False

    video = db.query(Video).filter(Video.video_id == video_id).first()
    if not video or not video.channel_id:
        return False

    # 전체 평균 VPH
    if avg_vph is None:
        avg_vph = db.query(func.avg(Analysis.vph)).scalar() or 0

    is_high_vph = current_vph > max(avg_vph * 2, 100)
    if not is_high_vph:
        return False

    # 구독자 수로 판별 (있을 때)
    if video.subscriber_count and video.subscriber_count > 0:
        return video.subscriber_count <= 100000

    # 구독자 수 없으면 기존 방식: 채널 영상 수로 판별
    channel_video_count = (
        db.query(func.count(Video.video_id))
        .filter(Video.channel_id == video.channel_id)
        .scalar()
    ) or 0

    return channel_video_count <= 3
