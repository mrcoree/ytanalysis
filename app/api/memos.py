from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import VideoMemo, User
from app.api.shared import get_current_user

router = APIRouter()


class MemoRequest(BaseModel):
    content: str


@router.get("/memo/{video_id}")
def get_memo(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    memo = db.query(VideoMemo).filter(
        VideoMemo.user_id == current_user.id,
        VideoMemo.video_id == video_id,
    ).first()
    return {
        "video_id": video_id,
        "content": memo.content if memo else "",
        "updated_at": memo.updated_at if memo else None,
    }


@router.put("/memo/{video_id}")
def save_memo(
    video_id: str,
    req: MemoRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    memo = db.query(VideoMemo).filter(
        VideoMemo.user_id == current_user.id,
        VideoMemo.video_id == video_id,
    ).first()
    if memo:
        memo.content = req.content
    else:
        memo = VideoMemo(user_id=current_user.id, video_id=video_id, content=req.content)
        db.add(memo)
    db.commit()
    return {"ok": True, "video_id": video_id}
