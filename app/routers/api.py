"""Token-authenticated integrations API."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Member, Profile, User

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/members")
def members(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    token = get_settings().arronax_sync_token
    if not token:
        raise HTTPException(status_code=404, detail="Not Found")

    parts = (authorization or "").split()
    if (
        len(parts) != 2
        or parts[0].lower() != "bearer"
        or not secrets.compare_digest(parts[1].encode(), token.encode())
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    rows = db.execute(
        select(Member, User, Profile)
        .join(User, User.id == Member.user_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .order_by(Member.created_at)
    ).all()
    exported = []
    for member, user, profile in rows:
        exported.append({
            "id": user.id,
            "email": user.email.lower(),
            "status": member.status,
            "name": profile.display_name if profile else "",
            "role": profile.role if profile else "",
            "organization": profile.organization if profile else "",
            "headline": profile.headline if profile else "",
            "linkedin": profile.linkedin if profile else "",
            "website": profile.website if profile else "",
            "working_on": profile.working_on if profile else "",
            "interests": profile.interests if profile else [],
            "expertise": profile.expertise if profile else [],
            "joined_at": (member.approved_at or member.created_at).isoformat(),
            "updated_at": member.updated_at.isoformat(),
        })
    return {"members": exported}
