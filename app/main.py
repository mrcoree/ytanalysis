from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.db.database import engine, Base, SessionLocal
from app.api import auth, videos, analytics, transcript, bookmarks, channels, memos, references, admin

# 테이블 자동 생성 + 누락 컬럼 자동 추가
Base.metadata.create_all(bind=engine)

def _auto_migrate():
    """모델에 정의된 컬럼이 DB에 없으면 자동으로 ALTER TABLE ADD COLUMN"""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing_cols:
                col_type = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and col.default.arg is not None and not callable(col.default.arg):
                    val = col.default.arg
                    if isinstance(val, bool):
                        default = f" DEFAULT {'TRUE' if val else 'FALSE'}"
                    elif isinstance(val, str):
                        # 안전한 이스케이프
                        default = f" DEFAULT '{val.replace(chr(39), chr(39)+chr(39))}'"
                    else:
                        default = f" DEFAULT {int(val)}"
                nullable = "" if col.nullable else " NOT NULL"
                # NOT NULL without default will fail on existing rows, make nullable
                if nullable and not default:
                    nullable = ""
                # 테이블/컬럼명은 모델에서만 오므로 안전, 따옴표로 감싸 예약어 충돌 방지
                sql = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}{default}{nullable}'
                with engine.begin() as conn:
                    conn.execute(text(sql))

try:
    _auto_migrate()
except Exception:
    pass  # 마이그레이션 실패해도 앱 시작은 진행

# admin 계정 자동 생성
def _ensure_admin():
    import bcrypt
    from app.db.models import User
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == "admin").first()
        if not existing:
            pw_hash = bcrypt.hashpw("qkrdudwk1021".encode(), bcrypt.gensalt()).decode()
            db.add(User(username="admin", password_hash=pw_hash, is_admin=True))
            db.commit()
        elif not existing.is_admin:
            existing.is_admin = True
            db.commit()
    finally:
        db.close()

_ensure_admin()

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Viral Radar",
    description="YouTube 떡상 영상 발견 엔진 - VPH 기반 바이럴 탐지",
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# === CORS 설정 ===
from app.config import get_settings as _get_settings
_settings = _get_settings()
_allowed_origins = (
    [o.strip() for o in _settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    if _settings.ALLOWED_ORIGINS
    else []
)
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )


# === 보안 헤더 미들웨어 ===
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)


app.include_router(auth.router, tags=["Auth"])
app.include_router(videos.router, tags=["Videos"])
app.include_router(analytics.router, tags=["Analytics"])
app.include_router(transcript.router, tags=["Transcript"])
app.include_router(bookmarks.router, tags=["Bookmarks"])
app.include_router(channels.router, tags=["Channels"])
app.include_router(memos.router, tags=["Memos"])
app.include_router(references.router, tags=["References"])
app.include_router(admin.router, tags=["Admin"])

# Static files
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", tags=["UI"])
def serve_ui():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "viral-radar"}
