from fastapi import APIRouter, Depends

from app.models.user import User
from app.routers.deps import get_current_user
from app.schemas.user import UserRead

router = APIRouter(tags=["me"])


@router.get("/me", response_model=UserRead)
def read_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
