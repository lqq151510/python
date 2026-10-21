import uuid

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile

from app import image_library
from app.api.deps import CurrentUser, SessionDep
from app.core.upload_limit import MAX_IMAGE_BYTES
from app.image_library import (
    ImageError,
    ImageSearchQuery,
    ImageSearchResponse,
    ImagesPublic,
    ImageUploadResponse,
    public_image,
)

router = APIRouter(prefix="/images", tags=["images"])


def _http_error(exc: ImageError) -> HTTPException:
    return HTTPException(
        exc.status_code, detail={"code": exc.code, "message": exc.message}
    )


@router.get("", response_model=ImagesPublic)
def read_images(session: SessionDep, current_user: CurrentUser) -> ImagesPublic:
    return image_library.list_images(session, current_user.id)


@router.post("", response_model=ImageUploadResponse, status_code=201)
def upload_image(
    session: SessionDep, current_user: CurrentUser, file: UploadFile, response: Response
) -> ImageUploadResponse:
    try:
        raw = file.file.read(MAX_IMAGE_BYTES + 1)
        asset, duplicate = image_library.store_image(
            session, current_user.id, file.filename or "", raw
        )
    except ImageError as exc:
        raise _http_error(exc) from None
    finally:
        file.file.close()
    if duplicate:
        response.status_code = 200
    return ImageUploadResponse(image=public_image(asset), duplicate=duplicate)


@router.post("/search", response_model=ImageSearchResponse)
def search_images(
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
    body: ImageSearchQuery,
) -> ImageSearchResponse:
    try:
        hits = image_library.search_text(
            session, current_user.id, body.query, body.limit
        )
    except ImageError as exc:
        raise _http_error(exc) from None
    return ImageSearchResponse(
        kind="text", hits=hits, request_id=request.state.request_id
    )


@router.post("/search-by-image", response_model=ImageSearchResponse)
def search_by_image(
    request: Request, session: SessionDep, current_user: CurrentUser, file: UploadFile
) -> ImageSearchResponse:
    try:
        raw = file.file.read(MAX_IMAGE_BYTES + 1)
        hits = image_library.search_image(
            session, current_user.id, file.filename or "", raw
        )
    except ImageError as exc:
        raise _http_error(exc) from None
    finally:
        file.file.close()
    return ImageSearchResponse(
        kind="image", hits=hits, request_id=request.state.request_id
    )


@router.get("/{image_id}/file")
def read_image_file(
    session: SessionDep, current_user: CurrentUser, image_id: uuid.UUID
) -> Response:
    try:
        asset = image_library.owned_asset(session, current_user.id, image_id)
        payload = image_library.read_bytes(current_user.id, asset)
    except ImageError as exc:
        raise _http_error(exc) from None
    return Response(payload, media_type=asset.media_type)


@router.delete("/{image_id}")
def delete_image(
    session: SessionDep, current_user: CurrentUser, image_id: uuid.UUID
) -> dict[str, str]:
    try:
        image_library.delete_image(session, current_user.id, image_id)
    except ImageError as exc:
        raise _http_error(exc) from None
    return {"message": "图片已删除。"}
