import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, func, select
from starlette.concurrency import run_in_threadpool

from app import aigc, aigc_runner
from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.models import AigcJob

router = APIRouter(prefix="/aigc", tags=["aigc"])

MAX_FIRST_FRAME_BYTES = 8 * 1024 * 1024
IMAGE_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
LIST_LIMIT = 20


class AigcJobDetail(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    prompt: str
    params: aigc.VideoParams
    progress: int
    status_message: str | None = None
    estimate_seconds: int
    eta_seconds: int | None = None
    output_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AigcJobSummary(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    progress: int
    prompt: str
    status_message: str | None = None
    error_code: str | None = None
    output_url: str | None = None
    created_at: datetime


class AigcConfig(BaseModel):
    enabled: bool
    runner_running: bool
    comfy_reachable: bool
    comfy_error: str | None = None
    active_jobs: int
    max_queue_depth: int
    defaults: dict[str, Any]
    limits: dict[str, Any]
    model_profiles: list[str]
    generation_profiles: list[str]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


def _ensure_enabled() -> None:
    if not settings.AIGC_ENABLED:
        raise _error(
            409,
            "aigc_disabled",
            "本地视频生成未开启。先在 .env 里设置 AIGC_ENABLED=true 并启动 ComfyUI。",
        )


def _ensure_comfy_reachable() -> None:
    with aigc.ComfyClient() as client:
        try:
            client.system_stats()
        except aigc.AigcError as exc:
            raise _error(503, exc.code, exc.message) from None


def _active_job_count(session: Session, owner_id: uuid.UUID) -> int:
    queued_like = [aigc.STATUS_QUEUED, aigc.STATUS_RUNNING]
    return int(
        session.exec(
            select(func.count())
            .select_from(AigcJob)
            .where(AigcJob.owner_id == owner_id)
            .where(col(AigcJob.status).in_(queued_like))
        ).one()
    )


def _ensure_queue_capacity(session: Session, owner_id: uuid.UUID) -> None:
    active = _active_job_count(session, owner_id)
    if active >= settings.AIGC_MAX_QUEUE_DEPTH:
        raise _error(
            429,
            "aigc_queue_full",
            f"队列里还有 {active} 条任务没跑完，等它们结束再提交"
            f"（上限 {settings.AIGC_MAX_QUEUE_DEPTH} 条）。",
        )


def _job_or_404(session: Session, owner_id: uuid.UUID, job_id: uuid.UUID) -> AigcJob:
    job = session.get(AigcJob, job_id)
    if job is None or job.owner_id != owner_id:
        raise HTTPException(404, detail="任务不存在")
    return job


def _output_url(job: AigcJob) -> str | None:
    if job.status != aigc.STATUS_SUCCEEDED or not job.output_relpath:
        return None
    return f"{settings.API_V1_STR}/aigc/jobs/{job.id}/video"


def _eta_seconds(job: AigcJob, params: aigc.VideoParams) -> int | None:
    if job.status != aigc.STATUS_RUNNING or job.started_at is None:
        return None
    started = job.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    elapsed = (datetime.now(UTC) - started).total_seconds()
    return max(0, int(params.estimate_seconds - elapsed))


def _detail(job: AigcJob) -> AigcJobDetail:
    params = aigc.VideoParams.from_json(job.params_json)
    return AigcJobDetail(
        id=job.id,
        kind=job.kind,
        status=job.status,
        prompt=job.prompt,
        params=params,
        progress=job.progress,
        status_message=job.status_message,
        estimate_seconds=int(params.estimate_seconds),
        eta_seconds=_eta_seconds(job, params),
        output_url=_output_url(job),
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _summary(job: AigcJob) -> AigcJobSummary:
    return AigcJobSummary(
        id=job.id,
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        prompt=job.prompt,
        status_message=job.status_message,
        error_code=job.error_code,
        output_url=_output_url(job),
        created_at=job.created_at,
    )


def _create_job(
    session: Session,
    *,
    owner_id: uuid.UUID,
    kind: str,
    prompt: str,
    params: aigc.VideoParams,
    input_image_relpath: str | None = None,
    input_image_name: str | None = None,
    job_id: uuid.UUID | None = None,
) -> AigcJob:
    job = AigcJob(
        id=job_id or uuid.uuid4(),
        owner_id=owner_id,
        kind=kind,
        status=aigc.STATUS_QUEUED,
        prompt=prompt,
        params_json=params.to_json(),
        progress=0,
        status_message="排队中",
        input_image_relpath=input_image_relpath,
        input_image_name=input_image_name,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    aigc_runner.ensure_runner_started()
    return job


@router.get("/config", response_model=AigcConfig)
def read_aigc_config(current_user: CurrentUser, session: SessionDep) -> AigcConfig:
    """返回本机生成能力状态，供前端决定是否允许提交。"""
    comfy_reachable = False
    comfy_error: str | None = None
    if settings.AIGC_ENABLED:
        with aigc.ComfyClient() as client:
            try:
                client.system_stats()
                comfy_reachable = True
            except aigc.AigcError as exc:
                comfy_error = exc.message
    defaults = aigc.VideoParams()
    return AigcConfig(
        enabled=settings.AIGC_ENABLED,
        runner_running=bool(aigc_runner.runner_status()["running"]),
        comfy_reachable=comfy_reachable,
        comfy_error=comfy_error,
        active_jobs=_active_job_count(session, current_user.id),
        max_queue_depth=settings.AIGC_MAX_QUEUE_DEPTH,
        defaults=defaults.model_dump(),
        limits={
            "width": [aigc.MIN_SIDE, aigc.MAX_WIDTH],
            "height": [aigc.MIN_SIDE, aigc.MAX_HEIGHT],
            "side_step": aigc.SIDE_STEP,
            "duration_seconds": [aigc.MIN_DURATION, aigc.MAX_DURATION],
            "max_prompt_chars": settings.AIGC_MAX_PROMPT_CHARS,
            "max_first_frame_bytes": MAX_FIRST_FRAME_BYTES,
        },
        model_profiles=list(aigc.MODEL_PROFILES),
        generation_profiles=list(aigc.GENERATION_PROFILES),
    )


@router.get("/jobs", response_model=list[AigcJobSummary])
def list_aigc_jobs(
    current_user: CurrentUser, session: SessionDep
) -> list[AigcJobSummary]:
    jobs = session.exec(
        select(AigcJob)
        .where(AigcJob.owner_id == current_user.id)
        .order_by(col(AigcJob.created_at).desc())
        .limit(LIST_LIMIT)
    ).all()
    return [_summary(job) for job in jobs]


@router.post("/jobs", response_model=AigcJobDetail, status_code=201)
def create_aigc_job(
    current_user: CurrentUser, session: SessionDep, body: aigc.AigcJobCreate
) -> AigcJobDetail:
    """文生视频：提交即返回 job_id，真正的生成在后台串行队列里跑。"""
    _ensure_enabled()
    if body.kind != aigc.KIND_TEXT2VIDEO:
        raise _error(
            400,
            "aigc_use_image_endpoint",
            "图生视频请用 POST /aigc/jobs/image 上传首帧图。",
        )
    _ensure_queue_capacity(session, current_user.id)
    _ensure_comfy_reachable()
    job = _create_job(
        session,
        owner_id=current_user.id,
        kind=aigc.KIND_TEXT2VIDEO,
        prompt=body.prompt,
        params=body.params,
    )
    return _detail(job)


def _persist_image_job(
    *,
    session: Session,
    user_id: uuid.UUID,
    prompt: str,
    params: aigc.VideoParams,
    content: bytes,
    suffix: str,
    filename: str,
) -> AigcJob:
    """同步落盘 + 建任务；异步路由通过线程池调用，避免阻塞事件循环。"""
    _ensure_queue_capacity(session, user_id)
    _ensure_comfy_reachable()

    job_id = uuid.uuid4()
    relpath = f"uploads/{user_id}/{job_id}{suffix}"
    destination = Path(settings.AIGC_OUTPUT_DIR) / relpath
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)

    return _create_job(
        session,
        owner_id=user_id,
        kind=aigc.KIND_IMAGE2VIDEO,
        prompt=prompt,
        params=params,
        input_image_relpath=relpath,
        input_image_name=(Path(filename).name or f"{job_id}{suffix}")[:255],
        job_id=job_id,
    )


@router.post("/jobs/image", response_model=AigcJobDetail, status_code=201)
async def create_aigc_image_job(
    current_user: CurrentUser,
    session: SessionDep,
    first_frame: UploadFile = File(...),
    prompt: str = Form(..., min_length=1, max_length=2000),
    params: str = Form("{}"),
) -> AigcJobDetail:
    """图生视频：首帧图先存本机，执行时再上传给 ComfyUI。"""
    _ensure_enabled()
    suffix = IMAGE_SUFFIXES.get(first_frame.content_type or "")
    if suffix is None:
        raise _error(
            415,
            "aigc_unsupported_image",
            "首帧图只支持 PNG、JPEG、WebP。",
        )
    content = await first_frame.read(MAX_FIRST_FRAME_BYTES + 1)
    if not content:
        raise _error(400, "aigc_empty_image", "首帧图是空文件。")
    if len(content) > MAX_FIRST_FRAME_BYTES:
        raise _error(
            413,
            "aigc_image_too_large",
            f"首帧图不能超过 {MAX_FIRST_FRAME_BYTES // (1024 * 1024)} MiB。",
        )
    try:
        parsed_params = aigc.VideoParams.model_validate_json(params or "{}")
    except ValidationError as exc:
        raise _error(
            422, "aigc_invalid_params", _first_validation_message(exc)
        ) from None

    job = await run_in_threadpool(
        _persist_image_job,
        session=session,
        user_id=current_user.id,
        prompt=prompt.strip(),
        params=parsed_params,
        content=content,
        suffix=suffix,
        filename=first_frame.filename or "",
    )
    return _detail(job)


@router.get("/jobs/{job_id}", response_model=AigcJobDetail)
def read_aigc_job(
    current_user: CurrentUser, session: SessionDep, job_id: uuid.UUID
) -> AigcJobDetail:
    return _detail(_job_or_404(session, current_user.id, job_id))


@router.post("/jobs/{job_id}/cancel", response_model=AigcJobDetail)
def cancel_aigc_job(
    current_user: CurrentUser, session: SessionDep, job_id: uuid.UUID
) -> AigcJobDetail:
    job = _job_or_404(session, current_user.id, job_id)
    if job.status in aigc.TERMINAL_STATUSES:
        return _detail(job)
    job.status = aigc.STATUS_CANCELED
    job.status_message = None
    job.finished_at = datetime.now(UTC)
    session.add(job)
    session.commit()
    session.refresh(job)
    return _detail(job)


@router.get("/jobs/{job_id}/video")
def read_aigc_video(
    current_user: CurrentUser, session: SessionDep, job_id: uuid.UUID
) -> FileResponse:
    job = _job_or_404(session, current_user.id, job_id)
    if job.status != aigc.STATUS_SUCCEEDED or not job.output_relpath:
        raise HTTPException(404, detail="视频还没生成完")
    path = Path(settings.AIGC_OUTPUT_DIR) / job.output_relpath
    if not path.is_file():
        raise HTTPException(404, detail="视频文件已丢失")
    return FileResponse(path, media_type="video/mp4")


@router.delete("/jobs/{job_id}", status_code=204)
def delete_aigc_job(
    current_user: CurrentUser, session: SessionDep, job_id: uuid.UUID
) -> None:
    job = _job_or_404(session, current_user.id, job_id)
    if job.status not in aigc.TERMINAL_STATUSES:
        raise _error(409, "aigc_job_active", "任务还在跑，先取消再删除。")
    root = Path(settings.AIGC_OUTPUT_DIR)
    for relpath in (job.output_relpath, job.input_image_relpath):
        if relpath:
            (root / relpath).unlink(missing_ok=True)
    session.delete(job)
    session.commit()


def _first_validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "参数不合法"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "参数不合法"))
    return f"{location or '参数'}：{message}" if location else message
