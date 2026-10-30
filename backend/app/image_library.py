"""按登录用户隔离的图片库。

命令行 zebao-rag 的 Chroma 集合没有 owner，网页不读那份索引，也不接受客户端路径。
没有已入库图片时直接返回空结果，不调用嵌入模型。
"""

import base64
import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.core.upload_limit import MAX_IMAGE_BYTES
from app.models import ImageAsset, User
from app.semantic import TOKEN_PATH

MAX_IMAGES_PER_USER = 20
EMBED_ATTEMPTS = 2


class ImageError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code, self.code, self.message = status_code, code, message
        super().__init__(code)


@dataclass(frozen=True)
class ImageEmbedding:
    """A vector and the embedding space returned in the same worker response."""

    vector: list[float]
    space_id: str


class ImagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    filename: str
    size_bytes: int
    media_type: str
    created_at: datetime


class ImagesPublic(BaseModel):
    data: list[ImagePublic]
    count: int


class ImageUploadResponse(BaseModel):
    image: ImagePublic
    duplicate: bool


class ImageHit(BaseModel):
    id: uuid.UUID
    filename: str
    media_type: str
    score: float


class ImageSearchResponse(BaseModel):
    kind: str
    hits: list[ImageHit]
    request_id: str


class ImageSearchQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=5)


def parse_image(filename: str, raw: bytes) -> tuple[str, str, str]:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise ImageError(422, "invalid_filename", "文件名不符合要求。")
    lowered = name.lower()
    if lowered.endswith((".jpg", ".jpeg")):
        media_type = "image/jpeg"
        magic = raw.startswith(b"\xff\xd8\xff")
    elif lowered.endswith(".png"):
        media_type = "image/png"
        magic = raw.startswith(b"\x89PNG\r\n\x1a\n")
    else:
        raise ImageError(415, "unsupported_image", "请上传 JPG 或 PNG 图片。")
    if not raw or not magic:
        raise ImageError(422, "invalid_image", "文件内容不是有效的 JPG 或 PNG。")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageError(413, "image_too_large", "单张图片不能超过 1.5 MB。")
    return name, media_type, hashlib.sha256(raw).hexdigest()


def embed_text(text: str) -> ImageEmbedding:
    return _embed({"kind": "text", "text": text, "is_query": True})


def embed_image(raw: bytes, *, attempts: int = EMBED_ATTEMPTS) -> ImageEmbedding:
    return _embed(
        {
            "kind": "image",
            "image_base64": base64.b64encode(raw).decode("ascii"),
            "is_query": False,
        },
        attempts=attempts,
    )


def list_images(session: Session, owner_id: uuid.UUID) -> ImagesPublic:
    rows = session.exec(
        select(ImageAsset)
        .where(ImageAsset.owner_id == owner_id)
        .order_by(col(ImageAsset.created_at).desc())
    ).all()
    count = session.exec(
        select(func.count())
        .select_from(ImageAsset)
        .where(ImageAsset.owner_id == owner_id)
    ).one()
    return ImagesPublic(
        data=[ImagePublic.model_validate(row) for row in rows], count=count
    )


def store_image(
    session: Session, owner_id: uuid.UUID, filename: str, raw: bytes
) -> tuple[ImageAsset, bool]:
    name, media_type, digest = parse_image(filename, raw)
    existing = _owned_digest(session, owner_id, digest)
    if existing:
        return existing, True
    # 嵌入失败时不占名额、不写文件。锁只包住最后的名额检查。
    embedding = embed_image(raw)
    session.exec(select(User).where(User.id == owner_id).with_for_update()).one()
    existing = _owned_digest(session, owner_id, digest)
    if existing:
        return existing, True
    count = session.exec(
        select(func.count())
        .select_from(ImageAsset)
        .where(ImageAsset.owner_id == owner_id)
    ).one()
    if count >= MAX_IMAGES_PER_USER:
        raise ImageError(
            409, "image_quota", "每位用户最多保存 20 张图片，请先删除不再需要的图片。"
        )
    asset = ImageAsset(
        owner_id=owner_id,
        filename=name,
        sha256=digest,
        size_bytes=len(raw),
        media_type=media_type,
        embedding_json=json.dumps(
            {"version": 1, "space_id": embedding.space_id, "vector": embedding.vector}
        ),
    )
    session.add(asset)
    session.flush()
    path = _asset_path(owner_id, asset.id, media_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        path.unlink(missing_ok=True)
        existing = _owned_digest(session, owner_id, digest)
        if existing:
            return existing, True
        raise
    session.refresh(asset)
    return asset, False


def search_text(
    session: Session, owner_id: uuid.UUID, query: str, limit: int = 5
) -> list[ImageHit]:
    assets = _owned_assets(session, owner_id)
    if not assets:
        return []
    # Fail before a model call when any historical vector has no valid identity.
    for asset in assets:
        _load_vector(asset.embedding_json)
    return _rank(assets, embed_text(query), limit)


def search_image(
    session: Session,
    owner_id: uuid.UUID,
    filename: str,
    raw: bytes,
    limit: int = 5,
) -> list[ImageHit]:
    parse_image(filename, raw)
    assets = _owned_assets(session, owner_id)
    if not assets:
        return []
    for asset in assets:
        _load_vector(asset.embedding_json)
    return _rank(assets, embed_image(raw), limit)


def reembed_image(session: Session, owner_id: uuid.UUID, image_id: uuid.UUID) -> None:
    """Re-encode one owned image, then atomically replace its unchanged vector."""
    asset = owned_asset(session, owner_id, image_id)
    filename = asset.filename
    digest = asset.sha256
    media_type = asset.media_type
    size_bytes = asset.size_bytes
    original_embedding = asset.embedding_json
    # Authentication and ownership queries open a transaction; release it before
    # reading the file and waiting for the model. Do not retain an ORM object.
    session.rollback()

    def read_verified_image() -> bytes:
        path = _asset_path(owner_id, image_id, media_type)
        try:
            with path.open("rb") as source:
                raw = source.read(MAX_IMAGE_BYTES + 1)
        except OSError:
            raise ImageError(
                404, "image_not_found", "图片文件不存在或无法读取。"
            ) from None
        _, actual_media_type, actual_digest = parse_image(filename, raw)
        if (
            actual_digest != digest
            or actual_media_type != media_type
            or len(raw) != size_bytes
        ):
            raise ImageError(
                409, "image_file_changed", "图片文件已变化，未更新检索向量。"
            )
        return raw

    payload = read_verified_image()
    # A user-triggered rebuild never automatically repeats model work.
    embedding = embed_image(payload, attempts=1)
    try:
        read_verified_image()
    except ImageError:
        raise ImageError(
            409, "image_rebuild_conflict", "图片文件已变化或被删除，请刷新后重试。"
        ) from None
    replacement = json.dumps(
        {
            "version": 2,
            "space_id": embedding.space_id,
            "vector": embedding.vector,
            "revision": uuid.uuid4().hex,
        }
    )
    try:
        result = session.execute(
            update(ImageAsset)
            .where(
                ImageAsset.id == image_id,
                ImageAsset.owner_id == owner_id,
                ImageAsset.sha256 == digest,
                ImageAsset.media_type == media_type,
                ImageAsset.size_bytes == size_bytes,
                ImageAsset.embedding_json == original_embedding,
            )
            .values(embedding_json=replacement)
        )
        if result.rowcount != 1:
            session.rollback()
            raise ImageError(
                409, "image_rebuild_conflict", "图片已变化或被删除，请刷新后重试。"
            )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise ImageError(
            503,
            "image_rebuild_unavailable",
            "检索向量的更新结果暂不可确认，请刷新后检查；原图未被修改。",
        ) from None


def owned_asset(
    session: Session, owner_id: uuid.UUID, image_id: uuid.UUID
) -> ImageAsset:
    asset = session.exec(
        select(ImageAsset).where(
            ImageAsset.id == image_id, ImageAsset.owner_id == owner_id
        )
    ).first()
    if asset is None:
        raise ImageError(404, "image_not_found", "图片不存在或已删除。")
    return asset


def read_bytes(owner_id: uuid.UUID, asset: ImageAsset) -> bytes:
    path = _asset_path(owner_id, asset.id, asset.media_type)
    if not path.is_file():
        raise ImageError(404, "image_not_found", "图片不存在或已删除。")
    return path.read_bytes()


def delete_image(session: Session, owner_id: uuid.UUID, image_id: uuid.UUID) -> None:
    asset = owned_asset(session, owner_id, image_id)
    path = _asset_path(owner_id, asset.id, asset.media_type)
    session.delete(asset)
    session.commit()
    path.unlink(missing_ok=True)


def _owned_digest(
    session: Session, owner_id: uuid.UUID, digest: str
) -> ImageAsset | None:
    return session.exec(
        select(ImageAsset).where(
            ImageAsset.owner_id == owner_id, ImageAsset.sha256 == digest
        )
    ).first()


def _owned_assets(session: Session, owner_id: uuid.UUID) -> list[ImageAsset]:
    return list(
        session.exec(
            select(ImageAsset)
            .where(ImageAsset.owner_id == owner_id)
            .order_by(col(ImageAsset.created_at), col(ImageAsset.id))
        ).all()
    )


def _asset_path(owner_id: uuid.UUID, image_id: uuid.UUID, media_type: str) -> Path:
    suffix = ".png" if media_type == "image/png" else ".jpg"
    root = Path(settings.IMAGE_LIBRARY_DIR).resolve()
    directory = (root / str(owner_id)).resolve()
    path = (directory / f"{image_id}{suffix}").resolve()
    if path.parent != directory:
        raise ImageError(400, "invalid_image_path", "图片路径不合法。")
    return path


def _rank(
    assets: list[ImageAsset], query: ImageEmbedding, limit: int
) -> list[ImageHit]:
    scored: list[tuple[float, ImageAsset]] = []
    for asset in assets:
        stored = _load_vector(asset.embedding_json)
        if stored.space_id != query.space_id or len(stored.vector) != len(query.vector):
            raise ImageError(
                503,
                "embedding_mismatch",
                "图片向量与当前嵌入模型不一致，暂时无法检索；原图仍可查看。",
            )
        score = _cosine(stored.vector, query.vector)
        if score >= settings.RAG_MIN_SIMILARITY:
            scored.append((score, asset))
    scored.sort(key=lambda item: (-item[0], item[1].filename, str(item[1].id)))
    return [
        ImageHit(
            id=asset.id,
            filename=asset.filename,
            media_type=asset.media_type,
            score=round(score, 6),
        )
        for score, asset in scored[:limit]
    ]


def _load_vector(raw: str) -> ImageEmbedding:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ImageError(
            503, "embedding_corrupt", "已保存的图片向量无法读取。"
        ) from None
    if isinstance(parsed, list):
        # Historical vectors have no identity; do not guess which model made them.
        raise ImageError(
            409,
            "embedding_identity_unknown",
            "旧图片向量的模型身份未知，暂时无法检索；原图仍可查看。",
        )
    if not isinstance(parsed, dict):
        raise ImageError(503, "embedding_corrupt", "已保存的图片向量无法读取。")
    version = parsed.get("version")
    if type(version) is not int or version not in (1, 2):
        raise ImageError(503, "embedding_corrupt", "已保存的图片向量版本不受支持。")
    fields = {"version", "space_id", "vector"}
    if version == 2:
        fields.add("revision")
    if set(parsed) != fields:
        raise ImageError(503, "embedding_corrupt", "已保存的图片向量无法读取。")
    if version == 2:
        _require_hex(parsed["revision"], 32)
    space_id = _require_hex(parsed["space_id"], 64)
    return ImageEmbedding(vector=_require_vector(parsed["vector"]), space_id=space_id)


def _embed(
    payload: dict[str, object], *, attempts: int = EMBED_ATTEMPTS
) -> ImageEmbedding:
    if not settings.RAG_WORKER_ENABLED:
        raise ImageError(503, "embedding_disabled", "未启用嵌入模型，图片检索不可用。")
    timeout = min(30.0, float(settings.RAG_WORKER_TIMEOUT_SECONDS))
    deadline = monotonic() + timeout
    for attempt in range(attempts):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ImageError(503, "embedding_timeout", "嵌入模型请求超时，请稍后重试。")
        try:
            return _embed_once(payload, remaining)
        except httpx.ConnectError:
            if attempt == attempts - 1:
                break
            continue
        except httpx.TimeoutException:
            raise ImageError(
                503, "embedding_timeout", "嵌入模型请求超时，请稍后重试。"
            ) from None
    raise ImageError(
        503, "embedding_unavailable", "暂时无法连接嵌入模型，请确认本地检索服务已启动。"
    )


def _embed_once(payload: dict[str, object], timeout: float) -> ImageEmbedding:
    try:
        token = TOKEN_PATH.read_text().strip()
    except OSError:
        token = ""
    if len(token) < 32:
        raise ImageError(503, "embedding_unavailable", "嵌入服务尚未就绪。")
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            response = client.post(
                str(settings.RAG_WORKER_URL).rstrip("/") + "/embed",
                json=payload,
                headers={"Authorization": "Bearer " + token},
                timeout=httpx.Timeout(timeout, connect=min(2.0, timeout)),
            )
    except httpx.ConnectError:
        raise
    except httpx.TimeoutException:
        raise
    except httpx.HTTPError:
        raise ImageError(503, "embedding_unavailable", "嵌入模型暂时不可用。") from None
    if response.status_code == 422:
        raise ImageError(422, "invalid_image", "嵌入模型拒绝了这张图片或这段文字。")
    if response.status_code >= 400:
        raise ImageError(
            503, "embedding_unavailable", "嵌入模型暂时不可用，请稍后重试。"
        )
    try:
        body = response.json()
    except ValueError:
        raise ImageError(503, "embedding_invalid", "嵌入模型返回了无效结果。") from None
    vector = body.get("vector") if isinstance(body, dict) else None
    identity = body.get("identity") if isinstance(body, dict) else None
    if not isinstance(identity, dict) or set(identity) != {
        "space_id",
        "protocol_id",
        "generation",
    }:
        raise ImageError(
            503, "embedding_identity_invalid", "嵌入服务未提供有效的模型身份。"
        )
    space_id = _require_hex(identity.get("space_id"), 64)
    _require_hex(identity.get("protocol_id"), 64)
    _require_hex(identity.get("generation"), 32)
    return ImageEmbedding(vector=_require_vector(vector), space_id=space_id)


def _require_hex(value: object, length: int) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None
    ):
        raise ImageError(
            503, "embedding_identity_invalid", "嵌入服务返回了无效的模型身份。"
        )
    return value


def _require_vector(value: object) -> list[float]:
    if not isinstance(value, list) or not 1 <= len(value) <= 4096:
        raise ImageError(503, "embedding_invalid", "嵌入模型返回了无效向量。")
    vector: list[float] = []
    for item in value:
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
        ):
            raise ImageError(503, "embedding_invalid", "嵌入模型返回了无效向量。")
        vector.append(float(item))
    return vector


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def public_image(asset: ImageAsset) -> ImagePublic:
    return ImagePublic.model_validate(asset)
