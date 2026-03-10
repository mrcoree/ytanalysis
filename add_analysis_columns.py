"""Analysis 테이블에 새 컬럼 추가 마이그레이션 스크립트"""
from app.db.database import engine
from sqlalchemy import text

statements = [
    "ALTER TABLE analysis ADD COLUMN IF NOT EXISTS predicted_views_24h BIGINT DEFAULT 0",
    "ALTER TABLE analysis ADD COLUMN IF NOT EXISTS predicted_views_7d BIGINT DEFAULT 0",
    "ALTER TABLE analysis ADD COLUMN IF NOT EXISTS growth_pattern VARCHAR DEFAULT 'unknown'",
    "ALTER TABLE analysis ADD COLUMN IF NOT EXISTS is_darkhorse BOOLEAN DEFAULT FALSE",
]

with engine.connect() as conn:
    for stmt in statements:
        conn.execute(text(stmt))
    conn.commit()
    print("Migration complete: added predicted_views_24h, predicted_views_7d, growth_pattern, is_darkhorse")
