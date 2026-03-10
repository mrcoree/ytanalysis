from celery import Celery
from celery.schedules import crontab
from app.config import get_settings

settings = get_settings()

celery = Celery(
    "viral_radar",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks"],
)

celery.conf.beat_schedule = {
    "collect-recent-stats-every-30-min": {
        "task": "app.tasks.collect_all_stats",
        "schedule": 1800.0,  # 30분 — 최근 7일 영상
    },
    "collect-mid-stats-every-2-hours": {
        "task": "app.tasks.collect_mid_stats",
        "schedule": 7200.0,  # 2시간 — 7~30일 영상
    },
    "collect-old-stats-daily": {
        "task": "app.tasks.collect_old_stats",
        "schedule": crontab(hour=5, minute=0),  # 매일 새벽 5시 — 30일+ 영상
    },
    "discover-videos-every-hour": {
        "task": "app.tasks.discover_trending",
        "schedule": 3600.0,  # 1시간
    },
    "check-channel-new-videos-every-15-min": {
        "task": "app.tasks.check_channel_new_videos",
        "schedule": 900.0,  # 15분
    },
    "cleanup-old-data-daily": {
        "task": "app.tasks.cleanup_old_data",
        "schedule": crontab(hour=4, minute=0),  # 매일 새벽 4시
    },
}

celery.conf.timezone = "Asia/Seoul"
