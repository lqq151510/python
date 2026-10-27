from typing import NoReturn

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser
from app.code_agent import (
    CodeAgentError,
    CodeApplyRequest,
    CodeDiffResult,
    CodeGenerateRequest,
    CodeGenerateResponse,
    GithubImportRequest,
    WorkspaceDiffRequest,
    WorkspaceFileWrite,
    apply_workspace_edits,
    delete_workspace_file,
    generate_code,
    get_model_config,
    import_github_repository,
    list_workspace,
    read_workspace_file,
    review_workspace_diff,
    save_workspace_file,
    store_workspace_uploads,
)

router = APIRouter(prefix="/code", tags=["code"])


def _raise_api_error(request: Request, error: CodeAgentError) -> NoReturn:
    request.state.chat_error_code = error.code
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from None


@router.get("/config")
def code_model_config(_current_user: CurrentUser):
    """Return non-secret model connection status for the code workspace UI."""
    return get_model_config()


@router.get("/workspace")
async def read_code_workspace(current_user: CurrentUser):
    return await run_in_threadpool(list_workspace, current_user.id)


@router.get("/workspace/files/{file_path:path}")
async def read_code_workspace_file(
    request: Request, current_user: CurrentUser, file_path: str
):
    try:
        content = await run_in_threadpool(
            read_workspace_file, current_user.id, file_path
        )
        return {"path": file_path, "content": content}
    except CodeAgentError as exc:
        _raise_api_error(request, exc)


@router.put("/workspace/files")
async def write_code_workspace_file(
    request: Request, current_user: CurrentUser, body: WorkspaceFileWrite
):
    try:
        return await run_in_threadpool(
            save_workspace_file, current_user.id, body.path, body.content
        )
    except CodeAgentError as exc:
        _raise_api_error(request, exc)


@router.delete("/workspace/files/{file_path:path}")
async def delete_code_workspace_file(
    request: Request, current_user: CurrentUser, file_path: str
):
    try:
        return await run_in_threadpool(delete_workspace_file, current_user.id, file_path)
    except CodeAgentError as exc:
        _raise_api_error(request, exc)


@router.post("/workspace/diff", response_model=CodeDiffResult)
async def diff_code_workspace_file(
    request: Request, current_user: CurrentUser, body: WorkspaceDiffRequest
):
    try:
        return await run_in_threadpool(
            review_workspace_diff, current_user.id, body.path, body.content, full=body.full
        )
    except CodeAgentError as exc:
        _raise_api_error(request, exc)


@router.post("/workspace/files")
async def upload_code_workspace_files(
    request: Request,
    current_user: CurrentUser,
    files: list[UploadFile] = File(...),
):
    uploads: list[tuple[str, bytes]] = []
    try:
        for file in files:
            content = await file.read(512 * 1024 + 1)
            uploads.append((file.filename or "", content))
        return await run_in_threadpool(store_workspace_uploads, current_user.id, uploads)
    except CodeAgentError as exc:
        _raise_api_error(request, exc)
    finally:
        for file in files:
            await file.close()


@router.post("/workspace/github")
async def import_public_github_repository(
    request: Request, current_user: CurrentUser, body: GithubImportRequest
):
    try:
        return await import_github_repository(current_user.id, body.repository)
    except CodeAgentError as exc:
        _raise_api_error(request, exc)


@router.post("/generate", response_model=CodeGenerateResponse)
async def create_code_generation(
    request: Request, current_user: CurrentUser, body: CodeGenerateRequest
) -> CodeGenerateResponse:
    request_id = request.state.request_id
    if body.permission == "full" and not body.full_permissions_acknowledged:
        _raise_api_error(
            request,
            CodeAgentError(
                403,
                "full_permissions_confirmation_required",
                "启用完整沙箱权限前，请先确认页面上的风险说明。",
            ),
        )
    try:
        return await generate_code(current_user.id, body, request_id)
    except CodeAgentError as exc:
        _raise_api_error(request, exc)


@router.post("/workspace/apply")
async def apply_code_generation(
    request: Request, current_user: CurrentUser, body: CodeApplyRequest
):
    if body.permission == "read_only":
        _raise_api_error(
            request,
            CodeAgentError(
                403, "workspace_read_only", "当前权限仅允许查看，不能写入工作区。"
            ),
        )
    if not body.approved:
        _raise_api_error(
            request,
            CodeAgentError(
                403, "workspace_approval_required", "写入前必须先审阅并确认这些改动。"
            ),
        )
    if body.permission == "full" and not body.full_permissions_acknowledged:
        _raise_api_error(
            request,
            CodeAgentError(
                403, "full_permissions_confirmation_required", "完整权限需要先确认风险说明。"
            ),
        )
    try:
        return await run_in_threadpool(
            apply_workspace_edits, current_user.id, body.edits
        )
    except CodeAgentError as exc:
        _raise_api_error(request, exc)
