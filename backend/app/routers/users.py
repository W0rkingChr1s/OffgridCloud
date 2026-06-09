"""Admin-only user management (create, list, update role/active/password)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import User
from ..schemas import UserCreate, UserOut, UserUpdate
from ..security import hash_password

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")
    user = User(
        email=payload.email,
        name=payload.name,
        role=payload.role,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Guard rails: an admin must not lock themselves out.
    if user.id == admin.id:
        if payload.active is False:
            raise HTTPException(status_code=400, detail="You cannot deactivate yourself")
        if payload.role is not None and payload.role != user.role:
            raise HTTPException(status_code=400, detail="You cannot change your own role")

    if payload.name is not None:
        user.name = payload.name
    if payload.role is not None:
        user.role = payload.role
    if payload.active is not None:
        user.active = payload.active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)

    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete yourself")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
