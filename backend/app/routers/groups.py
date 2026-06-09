"""Admin-only team/group management and membership."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..admin_ops import audit
from ..db import get_db
from ..deps import require_admin
from ..models import Group, GroupMembership, User
from ..schemas import GroupCreate, GroupMembersUpdate, GroupOut, GroupUpdate

router = APIRouter(prefix="/api/groups", tags=["groups"], dependencies=[Depends(require_admin)])


def _to_out(db: Session, group: Group) -> GroupOut:
    member_ids = list(
        db.scalars(select(GroupMembership.user_id).where(GroupMembership.group_id == group.id))
    )
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        member_ids=member_ids,
    )


def _get(db: Session, group_id: int) -> Group:
    group = db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.get("", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_db)) -> list[GroupOut]:
    groups = db.scalars(select(Group).order_by(Group.name)).all()
    return [_to_out(db, g) for g in groups]


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> GroupOut:
    if db.scalar(select(Group).where(Group.name == payload.name)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Name bereits vergeben")
    group = Group(name=payload.name, description=payload.description)
    db.add(group)
    db.commit()
    db.refresh(group)
    audit(db, admin, "group.create", group.name)
    return _to_out(db, group)


@router.patch("/{group_id}", response_model=GroupOut)
def update_group(
    group_id: int,
    payload: GroupUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupOut:
    group = _get(db, group_id)
    if payload.name is not None:
        group.name = payload.name
    if payload.description is not None:
        group.description = payload.description
    db.commit()
    db.refresh(group)
    return _to_out(db, group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_group(
    group_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    group = _get(db, group_id)
    name = group.name
    db.delete(group)
    db.commit()
    audit(db, admin, "group.delete", name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{group_id}/members", response_model=GroupOut)
def set_members(
    group_id: int,
    payload: GroupMembersUpdate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> GroupOut:
    group = _get(db, group_id)
    valid_ids = set(db.scalars(select(User.id).where(User.id.in_(payload.user_ids))))
    unknown = set(payload.user_ids) - valid_ids
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unbekannte Benutzer: {sorted(unknown)}")
    db.query(GroupMembership).filter(GroupMembership.group_id == group.id).delete()
    for uid in valid_ids:
        db.add(GroupMembership(group_id=group.id, user_id=uid))
    db.commit()
    return _to_out(db, group)
