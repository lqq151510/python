"""Exercise image repair through HTTP and disposable PostgreSQL, never a model."""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import image_library
from app.core.config import settings
from app.core.db import engine
from app.models import ImageAsset
from tests.api.routes.test_images_and_agent import IMAGES, JPEG, headers
from tests.utils.user import create_random_user

SPACE_ID = "a" * 64
NEW_SPACE_ID = "b" * 64


@dataclass
class StoredImage:
    image_id: uuid.UUID
    headers: dict[str, str]
    path: Path
    embedding: str


@pytest.fixture
def stored_image(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> StoredImage:
    monkeypatch.setattr(settings, "IMAGE_LIBRARY_DIR", tmp_path)
    owner = create_random_user(db)
    auth = headers(owner)
    # The upload and every subsequent model operation are synthetic.
    with monkeypatch.context() as upload_patch:
        upload_patch.setattr(
            image_library,
            "embed_image",
            lambda _raw: image_library.ImageEmbedding([1.0, 0.0], SPACE_ID),
        )
        response = client.post(
            IMAGES,
            headers=auth,
            files={"file": ("synthetic.jpg", JPEG, "image/jpeg")},
        )
    assert response.status_code == 201
    image_id = uuid.UUID(response.json()["image"]["id"])
    asset = db.get(ImageAsset, image_id)
    assert asset is not None
    return StoredImage(
        image_id,
        auth,
        tmp_path / str(owner.id) / f"{image_id}.jpg",
        asset.embedding_json,
    )


def saved_embedding(db: Session, image: StoredImage) -> str:
    db.expire_all()
    asset = db.get(ImageAsset, image.image_id)
    assert asset is not None
    return asset.embedding_json


def test_explicit_reembed_repairs_legacy_vector_without_changing_original(
    client: TestClient,
    db: Session,
    stored_image: StoredImage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = db.get(ImageAsset, stored_image.image_id)
    assert asset is not None
    asset.embedding_json = json.dumps([1.0, 0.0])
    db.add(asset)
    db.commit()
    embed = Mock(return_value=image_library.ImageEmbedding([0.0, 1.0], NEW_SPACE_ID))
    monkeypatch.setattr(image_library, "embed_image", embed)

    response = client.post(
        f"{IMAGES}/{stored_image.image_id}/reembed", headers=stored_image.headers
    )

    assert response.status_code == 200
    embed.assert_called_once_with(JPEG, attempts=1)
    updated = json.loads(saved_embedding(db, stored_image))
    assert updated["version"] == 2
    assert updated["space_id"] == NEW_SPACE_ID
    assert updated["vector"] == [0.0, 1.0]
    assert uuid.UUID(hex=updated["revision"]).hex == updated["revision"]
    original = client.get(
        f"{IMAGES}/{stored_image.image_id}/file", headers=stored_image.headers
    )
    assert original.status_code == 200 and original.content == JPEG
    query = Mock(return_value=image_library.ImageEmbedding([0.0, 1.0], NEW_SPACE_ID))
    monkeypatch.setattr(image_library, "embed_text", query)
    search = client.post(
        IMAGES + "/search", headers=stored_image.headers, json={"query": "test"}
    )
    assert search.status_code == 200
    assert [hit["id"] for hit in search.json()["hits"]] == [str(stored_image.image_id)]
    query.assert_called_once_with("test")


def test_reembed_refuses_other_owner_before_model_work(
    client: TestClient,
    db: Session,
    stored_image: StoredImage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = create_random_user(db)
    embed = Mock(side_effect=AssertionError("unowned images must not reach a model"))
    monkeypatch.setattr(image_library, "embed_image", embed)
    response = client.post(
        f"{IMAGES}/{stored_image.image_id}/reembed", headers=headers(other)
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "image_not_found"
    embed.assert_not_called()
    assert saved_embedding(db, stored_image) == stored_image.embedding
    assert stored_image.path.read_bytes() == JPEG


@pytest.mark.parametrize("missing", [False, True], ids=["changed", "missing"])
def test_reembed_rejects_changed_source_before_model_work(
    client: TestClient,
    db: Session,
    stored_image: StoredImage,
    monkeypatch: pytest.MonkeyPatch,
    missing: bool,
) -> None:
    if missing:
        stored_image.path.unlink()
    else:
        stored_image.path.write_bytes(JPEG + b"changed")
    embed = Mock(side_effect=AssertionError("invalid source must not reach a model"))
    monkeypatch.setattr(image_library, "embed_image", embed)
    response = client.post(
        f"{IMAGES}/{stored_image.image_id}/reembed", headers=stored_image.headers
    )
    assert response.status_code == (404 if missing else 409)
    assert response.json()["detail"]["code"] == (
        "image_not_found" if missing else "image_file_changed"
    )
    embed.assert_not_called()
    assert saved_embedding(db, stored_image) == stored_image.embedding


def test_reembed_detects_file_deletion_during_model_work(
    client: TestClient,
    db: Session,
    stored_image: StoredImage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def delete_source(_raw: bytes, *, attempts: int) -> image_library.ImageEmbedding:
        assert attempts == 1
        stored_image.path.unlink()
        return image_library.ImageEmbedding([0.0, 1.0], NEW_SPACE_ID)

    embed = Mock(side_effect=delete_source)
    monkeypatch.setattr(image_library, "embed_image", embed)
    response = client.post(
        f"{IMAGES}/{stored_image.image_id}/reembed", headers=stored_image.headers
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "image_rebuild_conflict"
    embed.assert_called_once_with(JPEG, attempts=1)
    assert saved_embedding(db, stored_image) == stored_image.embedding
    assert not stored_image.path.exists()


def test_reembed_does_not_overwrite_a_concurrently_committed_vector(
    client: TestClient,
    db: Session,
    stored_image: StoredImage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newer = json.dumps({"version": 1, "space_id": "c" * 64, "vector": [0.5, 0.5]})

    def concurrent_update(
        _raw: bytes, *, attempts: int
    ) -> image_library.ImageEmbedding:
        assert attempts == 1
        # A separate real transaction commits while the request waits for its model.
        with Session(engine) as concurrent:
            asset = concurrent.get(ImageAsset, stored_image.image_id)
            assert asset is not None
            asset.embedding_json = newer
            concurrent.add(asset)
            concurrent.commit()
        return image_library.ImageEmbedding([0.0, 1.0], NEW_SPACE_ID)

    embed = Mock(side_effect=concurrent_update)
    monkeypatch.setattr(image_library, "embed_image", embed)
    response = client.post(
        f"{IMAGES}/{stored_image.image_id}/reembed", headers=stored_image.headers
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "image_rebuild_conflict"
    embed.assert_called_once_with(JPEG, attempts=1)
    assert saved_embedding(db, stored_image) == newer
    assert stored_image.path.read_bytes() == JPEG


def test_reembed_connection_failure_is_not_retried_or_persisted(
    client: TestClient,
    db: Session,
    stored_image: StoredImage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RAG_WORKER_ENABLED", True)
    worker = Mock(side_effect=httpx.ConnectError("synthetic unavailable worker"))
    monkeypatch.setattr(image_library, "_embed_once", worker)
    response = client.post(
        f"{IMAGES}/{stored_image.image_id}/reembed", headers=stored_image.headers
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "embedding_unavailable"
    assert worker.call_count == 1
    assert saved_embedding(db, stored_image) == stored_image.embedding
    assert stored_image.path.read_bytes() == JPEG


@pytest.mark.parametrize(
    "raw,status,code",
    [
        ("[1.0, 0.0]", 409, "embedding_identity_unknown"),
        ("not-json", 503, "embedding_corrupt"),
        ("null", 503, "embedding_corrupt"),
        (
            json.dumps({"version": True, "space_id": SPACE_ID, "vector": [1.0]}),
            503,
            "embedding_corrupt",
        ),
        (
            json.dumps({"version": 2, "space_id": SPACE_ID, "vector": [1.0]}),
            503,
            "embedding_corrupt",
        ),
        (
            json.dumps({"version": 1, "space_id": "invalid", "vector": [1.0]}),
            503,
            "embedding_identity_invalid",
        ),
        (
            json.dumps({"version": 1, "space_id": SPACE_ID, "vector": [True]}),
            503,
            "embedding_invalid",
        ),
    ],
    ids=[
        "legacy",
        "invalid-json",
        "scalar",
        "boolean-version",
        "missing-revision",
        "invalid-space",
        "invalid-vector",
    ],
)
def test_invalid_stored_vector_blocks_search_but_preserves_original(
    client: TestClient,
    db: Session,
    stored_image: StoredImage,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    status: int,
    code: str,
) -> None:
    asset = db.get(ImageAsset, stored_image.image_id)
    assert asset is not None
    asset.embedding_json = raw
    db.add(asset)
    db.commit()
    embed = Mock(
        side_effect=AssertionError("invalid stored identity must skip the model")
    )
    monkeypatch.setattr(image_library, "embed_text", embed)
    response = client.post(
        IMAGES + "/search", headers=stored_image.headers, json={"query": "test"}
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    embed.assert_not_called()
    assert saved_embedding(db, stored_image) == raw
    original = client.get(
        f"{IMAGES}/{stored_image.image_id}/file", headers=stored_image.headers
    )
    assert original.status_code == 200 and original.content == JPEG
