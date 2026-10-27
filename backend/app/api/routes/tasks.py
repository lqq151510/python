import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    StudyTask,
    StudyTaskCreate,
    StudyTaskPublic,
    StudyTasksPublic,
    StudyTaskUpdate,
    get_datetime_utc,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])
MAX_TASKS = 40


def _clean(value: str) -> str:
    return value.strip()


def _owned_task(session: SessionDep, owner_id: uuid.UUID, task_id: uuid.UUID) -> StudyTask:
    task = session.get(StudyTask, task_id)
    if task is None or task.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="复习项不存在")
    return task


def _public_list(tasks: list[StudyTask]) -> StudyTasksPublic:
    data = [StudyTaskPublic.model_validate(task) for task in tasks]
    return StudyTasksPublic(
        data=data,
        count=len(data),
        open_count=sum(1 for task in data if not task.done),
    )


@router.get("", response_model=StudyTasksPublic)
def read_tasks(session: SessionDep, current_user: CurrentUser) -> Any:
    statement = (
        select(StudyTask)
        .where(StudyTask.owner_id == current_user.id)
        .order_by(col(StudyTask.done), col(StudyTask.updated_at).desc())
    )
    return _public_list(list(session.exec(statement).all()))


@router.post("", response_model=StudyTaskPublic, status_code=201)
def create_task(
    session: SessionDep, current_user: CurrentUser, body: StudyTaskCreate
) -> Any:
    title = _clean(body.title)
    if not title:
        raise HTTPException(status_code=422, detail="标题不能为空")
    existing = session.exec(
        select(StudyTask).where(StudyTask.owner_id == current_user.id)
    ).all()
    if len(existing) >= MAX_TASKS:
        raise HTTPException(status_code=409, detail="复习清单已达 40 条上限")
    task = StudyTask(title=title, detail=_clean(body.detail), owner_id=current_user.id)
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@router.patch("/{task_id}", response_model=StudyTaskPublic)
def update_task(
    session: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    body: StudyTaskUpdate,
) -> Any:
    task = _owned_task(session, current_user.id, task_id)
    if body.title is not None:
        title = _clean(body.title)
        if not title:
            raise HTTPException(status_code=422, detail="标题不能为空")
        task.title = title
    if body.detail is not None:
        task.detail = _clean(body.detail)
    if body.done is not None:
        task.done = body.done
    task.updated_at = get_datetime_utc()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@router.delete("/{task_id}", status_code=204)
def delete_task(
    session: SessionDep, current_user: CurrentUser, task_id: uuid.UUID
) -> None:
    task = _owned_task(session, current_user.id, task_id)
    session.delete(task)
    session.commit()
