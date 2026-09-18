"""Authenticated image delivery — no public URLs exist for member photos."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SEAT_STATUSES, Member, User
from ..security import require_user
from ..storage import get

router = APIRouter()


@router.get("/media/{key:path}")
def media(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    member = db.get(Member, user.id)
    allowed = user.is_admin or (member and member.status in SEAT_STATUSES)
    if not allowed:
        raise HTTPException(404)
    try:
        data, ctype = get(key)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404) from None
    return Response(data, media_type=ctype,
                    headers={"Cache-Control": "private, max-age=3600"})
