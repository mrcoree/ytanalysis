"""DB 마이그레이션: 사용자 인증 시스템 추가"""
import sys
from sqlalchemy import text
from app.db.database import engine


def q(table):
    """테이블명을 따옴표로 감싸기 (references 등 예약어 대응)"""
    return f'"{table}"'


def migrate():
    with engine.connect() as conn:
        # 1. users 테이블 생성
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR NOT NULL UNIQUE,
                password_hash VARCHAR NOT NULL,
                created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'utc')
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_username ON users (username)"))
        conn.commit()
        print("[OK] users 테이블 생성")

        # 2. 각 테이블에 user_id 컬럼 추가
        tables = [
            "bookmarks",
            "channel_blacklist",
            "channel_bookmarks",
            "channel_notifications",
            "video_memos",
            "references",
        ]

        for t in tables:
            try:
                conn.execute(text(f'ALTER TABLE {q(t)} ADD COLUMN user_id INTEGER'))
                conn.commit()
                print(f"[OK] {t}.user_id 컬럼 추가")
            except Exception as e:
                conn.rollback()
                if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                    print(f"[SKIP] {t}.user_id 이미 존재")
                else:
                    print(f"[WARN] {t}: {e}")

        # 3. 기존 데이터가 있으면 기본 사용자 생성 후 연결
        has_any_data = False
        for t in tables:
            row = conn.execute(text(f'SELECT COUNT(*) FROM {q(t)}')).fetchone()
            if row and row[0] > 0:
                has_any_data = True
                break

        if has_any_data:
            existing = conn.execute(text("SELECT id FROM users WHERE username = 'admin'")).fetchone()
            if not existing:
                import bcrypt
                pw_hash = bcrypt.hashpw(b"1021", bcrypt.gensalt()).decode()
                conn.execute(text("INSERT INTO users (username, password_hash) VALUES ('admin', :pw)"), {"pw": pw_hash})
                conn.commit()
                print("[OK] 기본 사용자 'admin' 생성 (비밀번호: 1021)")

            admin_id = conn.execute(text("SELECT id FROM users WHERE username = 'admin'")).fetchone()[0]

            for t in tables:
                conn.execute(text(f'UPDATE {q(t)} SET user_id = :uid WHERE user_id IS NULL'), {"uid": admin_id})
            conn.commit()
            print(f"[OK] 기존 데이터를 admin(id={admin_id})에 연결")

        # 4. user_id NOT NULL 설정
        for t in tables:
            try:
                conn.execute(text(f'ALTER TABLE {q(t)} ALTER COLUMN user_id SET NOT NULL'))
                conn.commit()
            except Exception:
                conn.rollback()

        # 5. FK 추가
        for t in tables:
            fk = f"fk_{t}_user_id"
            try:
                conn.execute(text(f'ALTER TABLE {q(t)} ADD CONSTRAINT {fk} FOREIGN KEY (user_id) REFERENCES users(id)'))
                conn.commit()
            except Exception:
                conn.rollback()

        # 6. 인덱스 추가
        for t in tables:
            try:
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS ix_{t}_user_id ON {q(t)} (user_id)'))
                conn.commit()
            except Exception:
                conn.rollback()

        # 7. 기존 단일 컬럼 unique constraint 제거
        old_constraints = {
            "bookmarks": ["bookmarks_video_id_key"],
            "channel_blacklist": ["channel_blacklist_channel_id_key"],
            "channel_bookmarks": ["channel_bookmarks_channel_id_key"],
            "video_memos": ["video_memos_video_id_key"],
        }
        for t, names in old_constraints.items():
            for cn in names:
                try:
                    conn.execute(text(f'ALTER TABLE {q(t)} DROP CONSTRAINT IF EXISTS {cn}'))
                    conn.commit()
                except Exception:
                    conn.rollback()

        # 8. 새 복합 unique constraint 추가
        new_constraints = {
            "bookmarks": ("uq_bookmarks_user_video", "user_id, video_id"),
            "channel_blacklist": ("uq_ch_blacklist_user_ch", "user_id, channel_id"),
            "channel_bookmarks": ("uq_ch_bookmarks_user_ch", "user_id, channel_id"),
            "channel_notifications": ("uq_ch_notif_user_ch_vid", "user_id, channel_id, video_id"),
            "video_memos": ("uq_memos_user_video", "user_id, video_id"),
        }
        for t, (name, cols) in new_constraints.items():
            try:
                conn.execute(text(f'ALTER TABLE {q(t)} ADD CONSTRAINT {name} UNIQUE ({cols})'))
                conn.commit()
                print(f"[OK] {t} unique constraint: ({cols})")
            except Exception as e:
                conn.rollback()
                if "already exists" in str(e).lower():
                    print(f"[SKIP] {t} constraint 이미 존재")
                else:
                    print(f"[WARN] {t}: {e}")

        print("\n마이그레이션 완료!")


if __name__ == "__main__":
    migrate()
