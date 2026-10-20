"""AIGC 任务执行器：单并发队列 + 状态落库。

ComfyUI 在本机同一时刻只能跑一条 H3 任务（32 GiB 统一内存，显存就是内存），
所以这里用一个守护线程串行消费 aigcjob 表里的 queued 任务，而不是开线程池。

约定：
- 只支持单进程部署（uvicorn --workers 1）。领任务用「带状态条件的 UPDATE」，
  多进程时不会重复执行同一条，但仍会并行跑两条 ComfyUI 任务。
- 进程重启后：queued 保持原样，running 会被 recover_interrupted_jobs 放回队列。
- 取消是协作式的：置 canceled 后，执行器在下一个轮询点调用 /interrupt 并落库。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from app import aigc
from app.core.config import settings
from app.core.db import engine
from app.models import AigcJob

logger = logging.getLogger(__name__)

_POLL_FLOOR_SECONDS = 0.05
_QUEUE_PROBE_EVERY = 3

_stop_event = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _claim_next_job(session: Session) -> AigcJob | None:
    """领一条排队中的任务。条件 UPDATE 保证同一条不会被领两次。"""
    while True:
        candidate = session.exec(
            select(AigcJob)
            .where(AigcJob.status == aigc.STATUS_QUEUED)
            .order_by(AigcJob.created_at)
            .limit(1)
        ).first()
        if candidate is None:
            return None
        result = session.execute(
            sa_update(AigcJob)
            .where(
                AigcJob.id == candidate.id,
                AigcJob.status == aigc.STATUS_QUEUED,
            )
            .values(status=aigc.STATUS_RUNNING, started_at=_now(), progress=1)
        )
        session.commit()
        if result.rowcount:
            session.refresh(candidate)
            return candidate


def recover_interrupted_jobs(session: Session) -> int:
    """把上次进程退出时卡在 running 的任务放回队列，返回改动条数。"""
    stuck = session.exec(
        select(AigcJob).where(AigcJob.status == aigc.STATUS_RUNNING)
    ).all()
    for job in stuck:
        job.status = aigc.STATUS_QUEUED
        job.progress = 0
        job.comfy_prompt_id = None
        job.status_message = "服务重启，重新排队"
        job.started_at = None
        session.add(job)
    if stuck:
        session.commit()
    return len(stuck)


def _cancel_requested(session: Session, job_id: uuid.UUID) -> bool:
    session.expire_all()
    current = session.get(AigcJob, job_id)
    return current is not None and current.status == aigc.STATUS_CANCELED


def _fail(session: Session, job: AigcJob, error: aigc.AigcError) -> None:
    job.status = aigc.STATUS_FAILED
    job.error_code = error.code
    job.error_message = aigc._clip_error(error.message)
    job.status_message = None
    job.finished_at = _now()
    session.add(job)
    session.commit()
    logger.warning("aigc job %s failed: %s", job.id, error.code)


def _finish(
    session: Session, job: AigcJob, *, relpath: str, elapsed: float
) -> None:
    job.status = aigc.STATUS_SUCCEEDED
    job.progress = 100
    job.output_relpath = relpath
    job.status_message = None
    job.error_code = None
    job.error_message = None
    job.finished_at = _now()
    session.add(job)
    session.commit()
    logger.info("aigc job %s succeeded in %.0fs", job.id, elapsed)


def _resolve_destination(job: AigcJob) -> tuple[Path, str]:
    root = Path(settings.AIGC_OUTPUT_DIR)
    relpath = f"{job.owner_id}/{job.id}.mp4"
    return root / relpath, relpath


def _queue_position(client: aigc.ComfyClient, prompt_id: str) -> int | None:
    data = client.queue()
    running = data.get("queue_running") or []
    pending = data.get("queue_pending") or []
    for index, item in enumerate(pending):
        if isinstance(item, (list, tuple)) and len(item) > 1 and item[1] == prompt_id:
            return index + 1
    for item in running:
        if isinstance(item, (list, tuple)) and len(item) > 1 and item[1] == prompt_id:
            return 0
    return None


def _upload_first_frame(client: aigc.ComfyClient, job: AigcJob) -> str:
    """把落在本机的首帧图传给 ComfyUI，返回 ComfyUI 侧的文件名。"""
    if not job.input_image_relpath:
        raise aigc.AigcError(409, "aigc_first_frame_missing", "图生视频缺少首帧图")
    path = Path(settings.AIGC_OUTPUT_DIR) / job.input_image_relpath
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise aigc.AigcError(
            409, "aigc_first_frame_missing", "首帧图文件已丢失，请重新上传"
        ) from exc
    if not content:
        raise aigc.AigcError(409, "aigc_first_frame_missing", "首帧图文件是空的")
    return client.upload_image(
        filename=job.input_image_name or path.name,
        content=content,
        content_type=aigc.IMAGE_CONTENT_TYPES.get(path.suffix.lower(), "image/png"),
    )


def run_job(job_id: uuid.UUID) -> None:
    """执行一条任务，全程自己开关数据库会话（在线程里跑）。"""
    with Session(engine) as session:
        job = session.get(AigcJob, job_id)
        if job is None or job.status != aigc.STATUS_RUNNING:
            return
        owner_id = job.owner_id
        try:
            params = aigc.VideoParams.from_json(job.params_json)
        except aigc.AigcError as exc:
            _fail(session, job, exc)
            return

        client = aigc.ComfyClient()
        started = time.monotonic()
        destination: Path | None = None
        try:
            client.system_stats()
            image_name = None
            if job.kind == aigc.KIND_IMAGE2VIDEO:
                image_name = _upload_first_frame(client, job)
            graph = aigc.build_prompt(
                prompt=job.prompt,
                params=params,
                filename_prefix=f"aigc/devassist_{owner_id}",
                image_name=image_name,
            )
            prompt_id = client.submit(graph)
            job.comfy_prompt_id = prompt_id
            job.status_message = "已提交 ComfyUI，等待执行"
            session.add(job)
            session.commit()

            deadline = started + settings.AIGC_JOB_TIMEOUT_SECONDS
            poll_index = 0
            while True:
                if _cancel_requested(session, job_id):
                    client.interrupt()
                    job = session.get(AigcJob, job_id)
                    if job is not None:
                        job.status = aigc.STATUS_CANCELED
                        job.status_message = None
                        job.finished_at = _now()
                        session.add(job)
                        session.commit()
                    return

                entry = aigc.history_entry(client.history(prompt_id), prompt_id)
                if entry is not None:
                    status = aigc.history_status(entry)
                    if status == "error":
                        raise aigc.AigcError(
                            502, "aigc_execution_failed", aigc.history_error(entry)
                        )
                    if status == "success":
                        videos = aigc.saved_videos(entry)
                        if not videos:
                            raise aigc.AigcError(
                                502, "aigc_output_missing", "任务完成但没有视频产物"
                            )
                        video = videos[0]
                        destination, relpath = _resolve_destination(job)
                        client.download(
                            filename=video["filename"],
                            subfolder=str(video.get("subfolder") or ""),
                            folder_type=str(video.get("type") or "output"),
                            destination=destination,
                        )
                        _finish(
                            session,
                            job,
                            relpath=relpath,
                            elapsed=time.monotonic() - started,
                        )
                        return

                if time.monotonic() > deadline:
                    client.interrupt()
                    raise aigc.AigcError(
                        504,
                        "aigc_timeout",
                        f"超过 {settings.AIGC_JOB_TIMEOUT_SECONDS} 秒仍未完成，已取消",
                    )

                if poll_index % _QUEUE_PROBE_EVERY == 0:
                    position = _queue_position(client, prompt_id)
                    if position is not None and position > 0:
                        job.status_message = f"ComfyUI 队列中，前面还有 {position - 1} 条"
                    else:
                        job.status_message = "模型推理中"
                job.progress = aigc.estimate_progress(
                    time.monotonic() - started, params.estimate_seconds
                )
                session.add(job)
                session.commit()
                poll_index += 1
                if _stop_event.wait(max(settings.AIGC_POLL_INTERVAL_SECONDS, _POLL_FLOOR_SECONDS)):
                    return
        except aigc.AigcError as exc:
            if destination is not None:
                destination.unlink(missing_ok=True)
            job = session.get(AigcJob, job_id)
            if job is not None:
                _fail(session, job, exc)
        except Exception as exc:  # noqa: BLE001 - 后台线程必须兜住所有异常
            logger.exception("aigc job %s crashed", job_id)
            job = session.get(AigcJob, job_id)
            if job is not None:
                _fail(
                    session,
                    job,
                    aigc.AigcError(500, "aigc_internal_error", f"内部错误：{exc}"),
                )
        finally:
            client.close()


def _worker_loop() -> None:
    logger.info("aigc runner started")
    while not _stop_event.is_set():
        try:
            with Session(engine) as session:
                job = _claim_next_job(session)
            if job is None:
                _stop_event.wait(max(settings.AIGC_POLL_INTERVAL_SECONDS, _POLL_FLOOR_SECONDS))
                continue
            run_job(job.id)
        except Exception:  # noqa: BLE001 - 线程不能死
            logger.exception("aigc runner loop error")
            _stop_event.wait(max(settings.AIGC_POLL_INTERVAL_SECONDS, _POLL_FLOOR_SECONDS))
    logger.info("aigc runner stopped")


def ensure_runner_started() -> bool:
    """幂等启动执行器线程；返回是否在运行。"""
    global _thread
    if not settings.AIGC_ENABLED:
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop_event.clear()
        _thread = threading.Thread(
            target=_worker_loop, name="aigc-runner", daemon=True
        )
        _thread.start()
        return True


def stop_runner() -> None:
    global _thread
    with _lock:
        _stop_event.set()
        if _thread is not None:
            _thread.join(timeout=5)
            _thread = None


def runner_status() -> dict[str, object]:
    return {
        "enabled": settings.AIGC_ENABLED,
        "running": bool(_thread is not None and _thread.is_alive()),
    }
