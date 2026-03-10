def calculate_viral_score(
    vph: float,
    views: int,
    likes: int,
    comments: int,
    subscriber_count: int = 0,
) -> float:
    """바이럴 스코어 계산

    score = (vph * 0.4) + (engagement * 100 * 0.3) + (sub_ratio * 100 * 0.3)

    engagement = (likes + comments) / views (조회수 대비 반응률)
    sub_ratio = views / subscriber_count (구독자 대비 조회수 비율)
    구독자 정보가 없으면 views 기반으로 대체 계산.
    """
    if views <= 0:
        return 0.0

    engagement = (likes + comments) / views

    if subscriber_count > 0:
        # 구독자 대비 조회수 비율 (1.0 = 구독자 수만큼 조회, 10.0 = 구독자 10배)
        sub_ratio = min(views / subscriber_count, 10.0) / 10.0
    else:
        # 구독자 정보 없으면 조회수 규모로 대체
        sub_ratio = min(views / 100000, 1.0)

    score = (vph * 0.4) + (engagement * 100 * 0.3) + (sub_ratio * 100 * 0.3)
    return round(score, 2)
