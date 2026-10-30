"""Offline image-identity tests; no application database or real model is used."""

import json
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import image_library as images
from app.api.deps import get_current_user, get_db
from app.api.routes.images import router as images_router
from app.models import ImageAsset, User

SPACE_A = "a" * 64
SPACE_B = "b" * 64
JPEG = b"\xff\xd8\xffimage-a"


class ImageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine, tables=[User.__table__, ImageAsset.__table__]
        )
        self.session = Session(self.engine)
        self.owner = User(email="offline@example.test", hashed_password="unused")
        self.session.add(self.owner)
        self.session.commit()
        self.settings = patch.object(
            images.settings, "IMAGE_LIBRARY_DIR", Path(self.directory.name)
        )
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.session.close()
        self.engine.dispose()
        self.directory.cleanup()

    def store(self):
        with patch.object(
            images,
            "embed_image",
            return_value=images.ImageEmbedding([1.0, 0.0], SPACE_A),
        ):
            return images.store_image(self.session, self.owner.id, "photo.jpg", JPEG)[0]

    def test_same_space_search_and_duplicate_keeps_existing_vector(self):
        asset = self.store()
        stored = json.loads(asset.embedding_json)
        self.assertEqual(
            stored, {"version": 1, "space_id": SPACE_A, "vector": [1.0, 0.0]}
        )
        with patch.object(
            images,
            "embed_text",
            return_value=images.ImageEmbedding([1.0, 0.0], SPACE_A),
        ):
            self.assertEqual(
                [
                    hit.id
                    for hit in images.search_text(self.session, self.owner.id, "x")
                ],
                [asset.id],
            )
        with patch.object(
            images,
            "embed_image",
            side_effect=AssertionError("duplicate must not re-embed"),
        ):
            duplicate, reused = images.store_image(
                self.session, self.owner.id, "photo.jpg", JPEG
            )
        self.assertTrue(reused)
        self.assertEqual(duplicate.embedding_json, asset.embedding_json)

    def test_same_dimension_different_space_is_rejected_without_file_loss(self):
        asset = self.store()
        with patch.object(
            images,
            "embed_text",
            return_value=images.ImageEmbedding([1.0, 0.0], SPACE_B),
        ):
            with self.assertRaises(images.ImageError) as error:
                images.search_text(self.session, self.owner.id, "x")
        self.assertEqual(error.exception.code, "embedding_mismatch")
        self.assertEqual(images.read_bytes(self.owner.id, asset), JPEG)
        self.assertEqual(len(images.list_images(self.session, self.owner.id).data), 1)

    def test_one_mismatching_image_rejects_the_entire_search(self):
        first = self.store()
        second = ImageAsset(
            owner_id=self.owner.id,
            filename="other.jpg",
            sha256="f" * 64,
            size_bytes=1,
            media_type="image/jpeg",
            embedding_json=json.dumps(
                {"version": 1, "space_id": SPACE_B, "vector": [1.0, 0.0]}
            ),
        )
        with self.assertRaises(images.ImageError) as error:
            images._rank([first, second], images.ImageEmbedding([1.0, 0.0], SPACE_A), 5)
        self.assertEqual(error.exception.code, "embedding_mismatch")
        self.assertEqual(images.read_bytes(self.owner.id, first), JPEG)

    def test_legacy_array_is_unknown_but_original_remains_accessible(self):
        asset = self.store()
        asset.embedding_json = json.dumps([1.0, 0.0])
        self.session.add(asset)
        self.session.commit()
        with patch.object(
            images,
            "embed_text",
            side_effect=AssertionError("legacy vector must skip the model"),
        ):
            with self.assertRaises(images.ImageError) as error:
                images.search_text(self.session, self.owner.id, "x")
        self.assertEqual(error.exception.code, "embedding_identity_unknown")
        self.assertEqual(images.read_bytes(self.owner.id, asset), JPEG)
        images.delete_image(self.session, self.owner.id, asset.id)
        self.assertEqual(len(images.list_images(self.session, self.owner.id).data), 0)

    def test_reembed_legacy_only_after_transaction_ends(self):
        asset = self.store()
        asset.embedding_json = json.dumps([1.0, 0.0])
        self.session.add(asset)
        self.session.commit()
        owner_id, image_id = self.owner.id, asset.id

        def embed(raw, *, attempts):
            self.assertEqual(attempts, 1)
            self.assertFalse(self.session.in_transaction())
            self.assertEqual(raw, JPEG)
            return images.ImageEmbedding([0.0, 1.0], SPACE_B)

        with patch.object(images, "embed_image", side_effect=embed):
            images.reembed_image(self.session, owner_id, image_id)
        self.session.expire_all()
        updated = images.owned_asset(self.session, owner_id, image_id)
        envelope = json.loads(updated.embedding_json)
        self.assertEqual(envelope["version"], 2)
        self.assertEqual(envelope["space_id"], SPACE_B)
        self.assertEqual(len(envelope["revision"]), 32)
        self.assertEqual(images.read_bytes(owner_id, updated), JPEG)
        self.assertEqual(images._load_vector(updated.embedding_json).space_id, SPACE_B)

    def test_reembed_other_owner_and_model_failure_preserve_data(self):
        asset = self.store()
        owner_id, image_id, original = self.owner.id, asset.id, asset.embedding_json
        with patch.object(
            images, "embed_image", side_effect=AssertionError("not owned")
        ):
            with self.assertRaises(images.ImageError) as error:
                images.reembed_image(self.session, uuid.uuid4(), image_id)
        self.assertEqual(error.exception.status_code, 404)
        with patch.object(
            images,
            "embed_image",
            side_effect=images.ImageError(503, "offline", "offline"),
        ):
            with self.assertRaises(images.ImageError):
                images.reembed_image(self.session, owner_id, image_id)
        self.session.expire_all()
        self.assertEqual(
            images.owned_asset(self.session, owner_id, image_id).embedding_json,
            original,
        )
        self.assertEqual(images.read_bytes(owner_id, asset), JPEG)

    def test_reembed_connection_failure_is_not_automatically_retried(self):
        asset = self.store()
        owner_id, image_id, original = self.owner.id, asset.id, asset.embedding_json
        with (
            patch.object(images.settings, "RAG_WORKER_ENABLED", True),
            patch.object(
                images, "_embed_once", side_effect=httpx.ConnectError("offline")
            ) as call,
        ):
            with self.assertRaises(images.ImageError) as error:
                images.reembed_image(self.session, owner_id, image_id)
        self.assertEqual(error.exception.code, "embedding_unavailable")
        self.assertEqual(call.call_count, 1)
        self.session.expire_all()
        self.assertEqual(
            images.owned_asset(self.session, owner_id, image_id).embedding_json,
            original,
        )

    def test_reembed_changed_file_fails_before_model_and_keeps_vector(self):
        asset = self.store()
        owner_id, image_id, original = self.owner.id, asset.id, asset.embedding_json
        path = images._asset_path(owner_id, image_id, asset.media_type)
        path.write_bytes(JPEG + b"changed")
        with patch.object(
            images, "embed_image", side_effect=AssertionError("must not call model")
        ):
            with self.assertRaises(images.ImageError) as error:
                images.reembed_image(self.session, owner_id, image_id)
        self.assertEqual(error.exception.code, "image_file_changed")
        self.session.expire_all()
        self.assertEqual(
            images.owned_asset(self.session, owner_id, image_id).embedding_json,
            original,
        )
        self.assertEqual(path.read_bytes(), JPEG + b"changed")

    def test_reembed_missing_or_oversized_file_skips_model(self):
        asset = self.store()
        owner_id, image_id, original = self.owner.id, asset.id, asset.embedding_json
        path = images._asset_path(owner_id, image_id, asset.media_type)
        for payload, expected in (
            (None, "image_not_found"),
            (JPEG + b"x" * images.MAX_IMAGE_BYTES, "image_too_large"),
        ):
            if payload is None:
                path.unlink()
            else:
                path.write_bytes(payload)
            with patch.object(
                images, "embed_image", side_effect=AssertionError("must not call model")
            ):
                with self.assertRaises(images.ImageError) as error:
                    images.reembed_image(self.session, owner_id, image_id)
            self.assertEqual(error.exception.code, expected)
            self.session.expire_all()
            self.assertEqual(
                images.owned_asset(self.session, owner_id, image_id).embedding_json,
                original,
            )

    def test_reembed_file_changes_during_model_call_preserve_vector(self):
        asset = self.store()
        owner_id, image_id, original = self.owner.id, asset.id, asset.embedding_json
        path = images._asset_path(owner_id, image_id, asset.media_type)

        def modify_file(_raw, *, attempts):
            self.assertEqual(attempts, 1)
            path.write_bytes(JPEG + b"changed")
            return images.ImageEmbedding([1.0, 0.0], SPACE_A)

        with patch.object(images, "embed_image", side_effect=modify_file):
            with self.assertRaises(images.ImageError) as error:
                images.reembed_image(self.session, owner_id, image_id)
        self.assertEqual(error.exception.code, "image_rebuild_conflict")
        self.session.expire_all()
        self.assertEqual(
            images.owned_asset(self.session, owner_id, image_id).embedding_json,
            original,
        )
        self.assertEqual(path.read_bytes(), JPEG + b"changed")

    def test_reembed_concurrent_update_does_not_overwrite(self):
        asset = self.store()
        owner_id, image_id = self.owner.id, asset.id
        newer = json.dumps({"version": 1, "space_id": SPACE_B, "vector": [0.0, 1.0]})

        def concurrent(_raw, *, attempts):
            self.assertEqual(attempts, 1)
            self.assertFalse(self.session.in_transaction())
            with Session(self.engine) as other:
                current = images.owned_asset(other, owner_id, image_id)
                current.embedding_json = newer
                other.add(current)
                other.commit()
            return images.ImageEmbedding([1.0, 0.0], SPACE_A)

        with patch.object(images, "embed_image", side_effect=concurrent):
            with self.assertRaises(images.ImageError) as error:
                images.reembed_image(self.session, owner_id, image_id)
        self.assertEqual(error.exception.code, "image_rebuild_conflict")
        self.session.expire_all()
        self.assertEqual(
            images.owned_asset(self.session, owner_id, image_id).embedding_json, newer
        )

    def test_reembed_concurrent_deletion_does_not_resurrect(self):
        asset = self.store()
        owner_id, image_id = self.owner.id, asset.id

        def concurrent(_raw, *, attempts):
            self.assertEqual(attempts, 1)
            with Session(self.engine) as other:
                images.delete_image(other, owner_id, image_id)
            return images.ImageEmbedding([1.0, 0.0], SPACE_A)

        with patch.object(images, "embed_image", side_effect=concurrent):
            with self.assertRaises(images.ImageError) as error:
                images.reembed_image(self.session, owner_id, image_id)
        self.assertEqual(error.exception.code, "image_rebuild_conflict")
        self.session.expire_all()
        self.assertEqual(len(images.list_images(self.session, owner_id).data), 0)

    def test_reembed_database_failure_rolls_back_and_preserves_vector(self):
        asset = self.store()
        owner_id, image_id, original = self.owner.id, asset.id, asset.embedding_json
        commit_patch = patch.object(
            self.session, "commit", side_effect=SQLAlchemyError("simulated")
        )

        def fail_commit(_raw, *, attempts):
            self.assertEqual(attempts, 1)
            commit_patch.start()
            return images.ImageEmbedding([0.0, 1.0], SPACE_B)

        try:
            with patch.object(images, "embed_image", side_effect=fail_commit):
                with self.assertRaises(images.ImageError) as error:
                    images.reembed_image(self.session, owner_id, image_id)
        finally:
            commit_patch.stop()
        self.assertEqual(error.exception.code, "image_rebuild_unavailable")
        self.assertFalse(self.session.in_transaction())
        self.assertEqual(
            images.owned_asset(self.session, owner_id, image_id).embedding_json,
            original,
        )
        self.assertEqual(images.read_bytes(owner_id, asset), JPEG)

    def test_malformed_envelopes_are_rejected(self):
        invalid = [
            {"version": True, "space_id": SPACE_A, "vector": [1.0]},
            {"version": 2, "space_id": SPACE_A, "vector": [1.0]},
            {"version": 1, "space_id": "A" * 64, "vector": [1.0]},
            {"version": 1, "space_id": SPACE_A, "vector": [True]},
            {"version": 1, "space_id": SPACE_A, "vector": [1.0], "extra": 1},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(images.ImageError):
                images._load_vector(json.dumps(payload))

    def test_embed_requires_complete_worker_identity(self):
        token = Path(self.directory.name) / "token"
        token.write_text("x" * 32)
        identity = {
            "space_id": SPACE_A,
            "protocol_id": "c" * 64,
            "generation": "d" * 32,
        }
        response = MagicMock(status_code=200)
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with (
            patch.object(images, "TOKEN_PATH", token),
            patch.object(images.httpx, "Client", return_value=client),
        ):
            response.json.return_value = {"vector": [1.0, 0.0], "identity": identity}
            self.assertEqual(
                images._embed_once({"kind": "text"}, 1),
                images.ImageEmbedding([1.0, 0.0], SPACE_A),
            )
            for bad_identity in (
                None,
                {},
                {**identity, "generation": "bad"},
                {**identity, "space_id": True},
                {**identity, "unexpected": "value"},
            ):
                response.json.return_value = {
                    "vector": [1.0, 0.0],
                    "identity": bad_identity,
                }
                with (
                    self.subTest(identity=bad_identity),
                    self.assertRaises(images.ImageError),
                ):
                    images._embed_once({"kind": "text"}, 1)

    def test_fake_worker_response_and_stored_image_share_space(self):
        # Load only the worker protocol from the repository; no model is loaded.
        source = str(Path(__file__).resolve().parents[2] / "services/zebao-rag/src")
        sys.path.insert(0, source)
        try:
            from zebao_rag.worker import Embedder, handler_type

            identity = {
                "space_id": SPACE_A,
                "protocol_id": "c" * 64,
                "generation": "d" * 32,
            }
            embedder = Embedder(lambda *a, **k: [[1.0, 0.0]], lambda _: [[1.0, 0.0]])
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), handler_type(None, "x" * 32, embedder, identity)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                token = Path(self.directory.name) / "token"
                token.write_text("x" * 32)
                with (
                    patch.object(images, "TOKEN_PATH", token),
                    patch.object(images.settings, "RAG_WORKER_ENABLED", True),
                    patch.object(
                        images.settings,
                        "RAG_WORKER_URL",
                        f"http://127.0.0.1:{server.server_port}",
                    ),
                ):
                    asset, _ = images.store_image(
                        self.session, self.owner.id, "photo.jpg", JPEG
                    )
                    self.assertEqual(
                        json.loads(asset.embedding_json)["space_id"], SPACE_A
                    )
                    self.assertEqual(
                        [
                            hit.id
                            for hit in images.search_text(
                                self.session, self.owner.id, "query"
                            )
                        ],
                        [asset.id],
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        finally:
            sys.path.remove(source)


class ImageReembedRouteTests(unittest.TestCase):
    def test_route_enforces_owner_and_maps_worker_failure(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(
            engine, tables=[User.__table__, ImageAsset.__table__]
        )
        app = FastAPI()
        app.include_router(images_router)
        owner_id = uuid.uuid4()
        current_owner = [owner_id]

        def db_override():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=current_owner[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(images.settings, "IMAGE_LIBRARY_DIR", Path(directory)):
                with Session(engine) as session:
                    owner = User(
                        id=current_owner[0],
                        email="route@example.test",
                        hashed_password="unused",
                    )
                    session.add(owner)
                    session.commit()
                    with patch.object(
                        images,
                        "embed_image",
                        return_value=images.ImageEmbedding([1.0, 0.0], SPACE_A),
                    ):
                        asset, _ = images.store_image(
                            session, owner.id, "photo.jpg", JPEG
                        )
                    image_id = asset.id
                with TestClient(app) as client:
                    url = f"/images/{image_id}/reembed"
                    current_owner[0] = uuid.uuid4()
                    with patch.object(
                        images, "embed_image", side_effect=AssertionError("wrong owner")
                    ):
                        self.assertEqual(client.post(url).status_code, 404)
                    current_owner[0] = owner_id
                    with patch.object(
                        images,
                        "embed_image",
                        side_effect=images.ImageError(503, "offline", "offline"),
                    ):
                        response = client.post(url)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()["detail"]["code"], "offline")
                    with patch.object(
                        images,
                        "embed_image",
                        return_value=images.ImageEmbedding([0.0, 1.0], SPACE_B),
                    ):
                        self.assertEqual(client.post(url).status_code, 200)
                with Session(engine) as session:
                    stored = images.owned_asset(session, owner_id, image_id)
                    self.assertEqual(
                        images._load_vector(stored.embedding_json).space_id, SPACE_B
                    )
                    self.assertEqual(images.read_bytes(owner_id, stored), JPEG)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
