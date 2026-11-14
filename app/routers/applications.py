from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.application import Application
from app.models.user import User
from app.routers.deps import get_current_user
from app.schemas.application import ApplicationRead

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("", response_model=list[ApplicationRead])
def list_applications(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[Application]:
    return db.query(Application).filter_by(is_active=True).order_by(Application.display_name).all()
