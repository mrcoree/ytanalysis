from sqlalchemy import Column, String, Integer, BigInteger, Float, DateTime, ForeignKey, Text, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    youtube_api_key = Column(String, default="")
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Video(Base):
    __tablename__ = "videos"

    video_id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    channel_id = Column(String, nullable=False)
    channel_title = Column(String, default="")
    description = Column(Text, default="")
    thumbnail = Column(String)
    published_at = Column(DateTime)
    duration = Column(String, default="")  # PT1H2M3S 형식
    tags = Column(Text, default="")  # 쉼표 구분 태그
    category_id = Column(String, default="")  # YouTube 카테고리 ID
    subscriber_count = Column(BigInteger, default=0)  # 채널 구독자 수

    stats = relationship("VideoStats", back_populates="video", order_by="VideoStats.collected_at.desc()")
    analysis = relationship("Analysis", back_populates="video", uselist=False)


class VideoStats(Base):
    __tablename__ = "video_stats"
    __table_args__ = (
        Index("idx_videostats_vid_collected", "video_id", "collected_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    views = Column(BigInteger, default=0)
    likes = Column(BigInteger, default=0)
    comments = Column(BigInteger, default=0)
    collected_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    video = relationship("Video", back_populates="stats")


class Analysis(Base):
    __tablename__ = "analysis"

    video_id = Column(String, ForeignKey("videos.video_id"), primary_key=True)
    vph = Column(Float, default=0.0)
    score = Column(Float, default=0.0)
    predicted_views_24h = Column(BigInteger, default=0)
    predicted_views_7d = Column(BigInteger, default=0)
    growth_pattern = Column(String, default="unknown")  # explosive/steady/plateau/declining/unknown
    is_darkhorse = Column(Boolean, default=False)

    video = relationship("Video", back_populates="analysis")


class SearchHistory(Base):
    __tablename__ = "search_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    searched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class VideoKeyword(Base):
    __tablename__ = "video_keywords"
    __table_args__ = (UniqueConstraint("video_id", "keyword"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    keyword = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "video_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    video = relationship("Video")


class ChannelBlacklist(Base):
    __tablename__ = "channel_blacklist"
    __table_args__ = (UniqueConstraint("user_id", "channel_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    channel_id = Column(String, nullable=False, index=True)
    channel_title = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ChannelBookmark(Base):
    __tablename__ = "channel_bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "channel_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    channel_id = Column(String, nullable=False, index=True)
    channel_title = Column(String, default="")
    thumbnail = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ChannelNotification(Base):
    __tablename__ = "channel_notifications"
    __table_args__ = (UniqueConstraint("user_id", "channel_id", "video_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    channel_id = Column(String, nullable=False, index=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    video = relationship("Video")


class VideoMemo(Base):
    __tablename__ = "video_memos"
    __table_args__ = (UniqueConstraint("user_id", "video_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    content = Column(Text, default="")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    video = relationship("Video")


class WatchedKeyword(Base):
    __tablename__ = "watched_keywords"
    __table_args__ = (UniqueConstraint("user_id", "keyword"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    keyword = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Reference(Base):
    __tablename__ = "references"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    my_title = Column(String, default="")
    my_thumbnail_idea = Column(Text, default="")
    my_concept = Column(Text, default="")
    status = Column(String, default="idea")  # idea/in_progress/done
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    video = relationship("Video")
