import html
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Reference, Video, Analysis, User
from app.api.shared import batch_latest_stats, get_current_user

router = APIRouter()


class ReferenceCreate(BaseModel):
    video_id: str
    my_title: str = ""
    my_concept: str = ""


class ReferenceUpdate(BaseModel):
    my_title: str | None = None
    my_thumbnail_idea: str | None = None
    my_concept: str | None = None
    status: str | None = None


class TitleScoreRequest(BaseModel):
    title: str
    video_id: str = ""


@router.get("/references")
def get_references(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    refs = db.query(Reference).filter(
        Reference.user_id == current_user.id,
    ).order_by(Reference.created_at.desc()).all()
    if not refs:
        return {"references": []}

    video_ids = [r.video_id for r in refs]
    videos = db.query(Video).filter(Video.video_id.in_(video_ids)).all()
    video_map = {v.video_id: v for v in videos}
    analyses = db.query(Analysis).filter(Analysis.video_id.in_(video_ids)).all()
    analysis_map = {a.video_id: a for a in analyses}
    stats_map = batch_latest_stats(db, video_ids)

    result = []
    for r in refs:
        v = video_map.get(r.video_id)
        if not v:
            continue
        a = analysis_map.get(r.video_id)
        latest = stats_map.get(r.video_id)
        result.append({
            "id": r.id,
            "video_id": r.video_id,
            "title": html.unescape(v.title or ""),
            "channel_title": v.channel_title or "",
            "thumbnail": v.thumbnail,
            "views": latest.views if latest else 0,
            "vph": a.vph if a else 0,
            "score": a.score if a else 0,
            "my_title": r.my_title,
            "my_thumbnail_idea": r.my_thumbnail_idea,
            "my_concept": r.my_concept,
            "status": r.status,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        })

    return {"references": result}


@router.post("/reference")
def add_reference(
    req: ReferenceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    video = db.query(Video).filter(Video.video_id == req.video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    existing = db.query(Reference).filter(
        Reference.user_id == current_user.id,
        Reference.video_id == req.video_id,
    ).first()
    if existing:
        return {"ok": True, "action": "already_exists", "id": existing.id}

    ref = Reference(user_id=current_user.id, video_id=req.video_id, my_title=req.my_title, my_concept=req.my_concept)
    db.add(ref)
    db.commit()
    return {"ok": True, "action": "added", "id": ref.id}


@router.put("/reference/{ref_id}")
def update_reference(
    ref_id: int,
    req: ReferenceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ref = db.query(Reference).filter(
        Reference.id == ref_id,
        Reference.user_id == current_user.id,
    ).first()
    if not ref:
        raise HTTPException(status_code=404, detail="Reference not found")

    if req.my_title is not None:
        ref.my_title = req.my_title
    if req.my_thumbnail_idea is not None:
        ref.my_thumbnail_idea = req.my_thumbnail_idea
    if req.my_concept is not None:
        ref.my_concept = req.my_concept
    if req.status is not None:
        ref.status = req.status
    db.commit()
    return {"ok": True}


@router.delete("/reference/{ref_id}")
def delete_reference(
    ref_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ref = db.query(Reference).filter(
        Reference.id == ref_id,
        Reference.user_id == current_user.id,
    ).first()
    if ref:
        db.delete(ref)
        db.commit()
    return {"ok": True}


@router.post("/title-score")
def score_title(
    req: TitleScoreRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from collections import Counter

    my_title = req.title.strip()
    video_id = req.video_id
    if not my_title:
        raise HTTPException(status_code=400, detail="Title is required")

    from sqlalchemy import desc
    rows = (
        db.query(Video.title, Analysis.vph)
        .join(Analysis, Video.video_id == Analysis.video_id)
        .filter(Analysis.vph > 0)
        .order_by(desc(Analysis.vph))
        .limit(200)
        .all()
    )

    stopwords = {
        "the", "a", "an", "is", "are", "in", "on", "at", "to", "for",
        "of", "and", "or", "with", "this", "that", "it", "by",
        "이", "그", "저", "것", "수", "등", "들", "및", "에", "의", "가", "을", "를",
        "은", "는", "로", "으로", "와", "과", "도", "에서", "한", "하는",
    }

    viral_words = Counter()
    bracket_patterns = Counter()
    title_lengths = []
    for title, vph in rows:
        words = re.findall(r'[가-힣]+|[a-zA-Z]+', title.lower())
        words = [w for w in words if len(w) >= 2 and w not in stopwords]
        for w in words:
            viral_words[w] += 1
        brackets = re.findall(r'[\[【\(](.*?)[\]】\)]', title)
        for b in brackets:
            if b.strip():
                bracket_patterns[b.strip()] += 1
        title_lengths.append(len(title))

    avg_title_len = sum(title_lengths) / len(title_lengths) if title_lengths else 30
    top_words = set(w for w, _ in viral_words.most_common(50))

    my_words = re.findall(r'[가-힣]+|[a-zA-Z]+', my_title.lower())
    my_words = [w for w in my_words if len(w) >= 2 and w not in stopwords]

    matching = [w for w in my_words if w in top_words]
    keyword_score = min(len(matching) / max(len(my_words), 1) * 40, 40)

    len_diff = abs(len(my_title) - avg_title_len) / avg_title_len
    length_score = max(20 - len_diff * 40, 0)

    has_bracket = bool(re.search(r'[\[【\(].*?[\]】\)]', my_title))
    bracket_score = 15 if has_bracket else 0

    has_number = bool(re.search(r'\d+', my_title))
    number_score = 10 if has_number else 0

    trigger_words = {"충격", "실화", "역대급", "미쳤", "대박", "ㄷㄷ", "레전드", "난리",
                     "최초", "긴급", "단독", "속보", "논란", "폭발", "경악", "극혐",
                     "감동", "소름", "반전", "꿀팁", "필수", "완벽", "최강", "혜자"}
    has_trigger = any(t in my_title for t in trigger_words)
    trigger_score = 15 if has_trigger else 0

    total_score = round(keyword_score + length_score + bracket_score + number_score + trigger_score, 1)

    tips = []
    if not has_bracket:
        top_brackets = [b for b, _ in bracket_patterns.most_common(3)]
        if top_brackets:
            tips.append(f"괄호 태그 추가 추천: [{top_brackets[0]}]")
    if not has_number:
        tips.append("숫자를 포함하면 클릭률이 올라갑니다 (예: TOP 5, 3가지)")
    if not has_trigger:
        tips.append("감정/자극 단어 추가 추천: 충격, 레전드, 대박 등")
    if len(my_title) > avg_title_len * 1.5:
        tips.append(f"제목이 긴 편입니다. 적정 길이: {int(avg_title_len)}자 내외")
    if len(my_title) < 10:
        tips.append("제목이 너무 짧습니다. 키워드를 더 추가하세요")
    if len(matching) == 0:
        tips.append("떡상 키워드가 없습니다. 트렌드 키워드를 포함해보세요")

    suggestions = _generate_title_suggestions(my_title, video_id, db, viral_words, bracket_patterns, trigger_words)

    return {
        "score": total_score,
        "breakdown": {
            "keyword_match": round(keyword_score, 1),
            "length": round(length_score, 1),
            "bracket_tag": bracket_score,
            "number": number_score,
            "trigger_word": trigger_score,
        },
        "matching_keywords": matching,
        "tips": tips,
        "suggestions": suggestions,
    }


def _generate_title_suggestions(my_title, video_id, db, viral_words, bracket_patterns, trigger_words):
    suggestions = []

    original_title = ""
    if video_id:
        video = db.query(Video).filter(Video.video_id == video_id).first()
        if video:
            original_title = video.title

    top_brackets = [b for b, _ in bracket_patterns.most_common(3)]
    top_keywords = [w for w, _ in viral_words.most_common(10)]
    my_words = re.findall(r'[가-힣]{2,}|[a-zA-Z]{2,}', my_title)

    if not my_words:
        return suggestions

    core = ' '.join(my_words[:3])

    if top_brackets:
        suggestions.append(f"[{top_brackets[0]}] {my_title}")

    suggestions.append(f"{core}, 이것만 알면 됩니다 TOP 5")

    triggers = list(trigger_words)[:3]
    if triggers:
        suggestions.append(f"{triggers[0]}.. {core} 이렇게 하면 대박납니다")

    if original_title:
        orig_brackets = re.findall(r'[\[【\(](.*?)[\]】\)]', original_title)
        orig_tag = f"[{orig_brackets[0].strip()}] " if orig_brackets else ""
        suggestions.append(f"{orig_tag}{core} 완벽 정리")

    suggestions.append(f"{core}, 아직도 모르세요?")

    return suggestions[:5]
