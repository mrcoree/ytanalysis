"""영상 인사이트 강화: 구독자 수, 태그, 카테고리, 키워드 연결 테이블 추가"""
from app.db.database import engine
from sqlalchemy import text

statements = [
    # Video 테이블에 새 컬럼 추가
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS tags TEXT DEFAULT ''",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS category_id VARCHAR DEFAULT ''",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS subscriber_count BIGINT DEFAULT 0",

    # 검색 키워드 ↔ 영상 연결 테이블
    """CREATE TABLE IF NOT EXISTS video_keywords (
        id SERIAL PRIMARY KEY,
        video_id VARCHAR NOT NULL REFERENCES videos(video_id),
        keyword VARCHAR NOT NULL,
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(video_id, keyword)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_video_keywords_video_id ON video_keywords(video_id)",
    "CREATE INDEX IF NOT EXISTS ix_video_keywords_keyword ON video_keywords(keyword)",
]

with engine.connect() as conn:
    for stmt in statements:
        conn.execute(text(stmt))
    conn.commit()
    print("Migration complete: added tags, category_id, subscriber_count, video_keywords table")
