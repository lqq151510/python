import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Note,
    NoteCreate,
    NotePublic,
    NotesPublic,
    NoteUpdate,
    get_datetime_utc,
)

router = APIRouter(prefix="/notes", tags=["notes"])
MAX_NOTES = 30


def _clean(value: str) -> str:
    return value.strip()


def _owned_note(session: SessionDep, owner_id: uuid.UUID, note_id: uuid.UUID) -> Note:
    note = session.get(Note, note_id)
    if note is None or note.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return note


@router.get("", response_model=NotesPublic)
def read_notes(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=30),
) -> Any:
    owner_id = current_user.id
    count_statement = (
        select(func.count()).select_from(Note).where(Note.owner_id == owner_id)
    )
    count = session.exec(count_statement).one()
    statement = (
        select(Note)
        .where(Note.owner_id == owner_id)
        .order_by(col(Note.updated_at).desc())
        .offset(skip)
        .limit(limit)
    )
    notes = session.exec(statement).all()
    return NotesPublic(
        data=[NotePublic.model_validate(note) for note in notes], count=count
    )


@router.post("", response_model=NotePublic, status_code=201)
def create_note(
    session: SessionDep, current_user: CurrentUser, body: NoteCreate
) -> Any:
    title = _clean(body.title)
    text = _clean(body.body)
    if not title or not text:
        raise HTTPException(status_code=422, detail="标题和内容不能为空")
    count_statement = (
        select(func.count()).select_from(Note).where(Note.owner_id == current_user.id)
    )
    if session.exec(count_statement).one() >= MAX_NOTES:
        raise HTTPException(status_code=409, detail="笔记已达 30 条上限")
    note = Note(title=title, body=text, owner_id=current_user.id)
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


@router.patch("/{note_id}", response_model=NotePublic)
def update_note(
    session: SessionDep,
    current_user: CurrentUser,
    note_id: uuid.UUID,
    body: NoteUpdate,
) -> Any:
    note = _owned_note(session, current_user.id, note_id)
    if body.title is not None:
        title = _clean(body.title)
        if not title:
            raise HTTPException(status_code=422, detail="标题不能为空")
        note.title = title
    if body.body is not None:
        text = _clean(body.body)
        if not text:
            raise HTTPException(status_code=422, detail="内容不能为空")
        note.body = text
    note.updated_at = get_datetime_utc()
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


@router.delete("/{note_id}", status_code=204)
def delete_note(
    session: SessionDep, current_user: CurrentUser, note_id: uuid.UUID
) -> None:
    note = _owned_note(session, current_user.id, note_id)
    session.delete(note)
    session.commit()
