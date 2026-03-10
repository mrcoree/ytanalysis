from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Bookmark, Video, Analysis, VideoStats, User
from app.api.shared import build_video_response, batch_latest_stats, get_current_user

router = APIRouter()


@router.get("/bookmarks")
def get_bookmarks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bookmarks = (
        db.query(Bookmark)
        .filter(Bookmark.user_id == current_user.id)
        .order_by(Bookmark.created_at.desc())
        .all()
    )

    if not bookmarks:
        return {"bookmarks": []}

    video_ids = [bm.video_id for bm in bookmarks]

    videos = db.query(Video).filter(Video.video_id.in_(video_ids)).all()
    video_map = {v.video_id: v for v in videos}

    analyses = db.query(Analysis).filter(Analysis.video_id.in_(video_ids)).all()
    analysis_map = {a.video_id: a for a in analyses}

    stats_map = batch_latest_stats(db, video_ids)

    result = []
    for bm in bookmarks:
        video = video_map.get(bm.video_id)
        if not video:
            continue
        resp = build_video_response(
            video,
            analysis=analysis_map.get(bm.video_id),
            latest_stat=stats_map.get(bm.video_id),
        )
        resp["bookmarked_at"] = bm.created_at
        result.append(resp)

    return {"bookmarks": result}


@router.post("/bookmark/{video_id}")
def add_bookmark(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    video = db.query(Video).filter(Video.video_id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    existing = db.query(Bookmark).filter(
        Bookmark.user_id == current_user.id,
        Bookmark.video_id == video_id,
    ).first()
    if existing:
        return {"ok": True, "action": "already_exists"}

    db.add(Bookmark(user_id=current_user.id, video_id=video_id))
    db.commit()
    return {"ok": True, "action": "added"}


@router.delete("/bookmark/{video_id}")
def remove_bookmark(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bm = db.query(Bookmark).filter(
        Bookmark.user_id == current_user.id,
        Bookmark.video_id == video_id,
    ).first()
    if bm:
        db.delete(bm)
        db.commit()
    return {"ok": True, "action": "removed"}
