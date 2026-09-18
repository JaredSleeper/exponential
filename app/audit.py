from sqlalchemy.orm import Session

from .models import AuditEvent, User


def audit(
    db: Session,
    action: str,
    actor: User | None = None,
    target_type: str = "",
    target_id: str = "",
    details: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_user_id=actor.id if actor else None,
            actor_email=actor.email if actor else "",
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )
    )
