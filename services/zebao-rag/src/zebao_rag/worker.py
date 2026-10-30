"""Loopback-only semantic ranking for DevAssist's already authorized chunks.

PostgreSQL remains the source of truth. Each request uses a temporary Chroma
collection containing only its supplied candidates; no private corpus is read.
Only normalized document embeddings are cached in bounded process memory.
"""

import argparse
import base64
import binascii
import hashlib
import json
import logging
import math
import os
import secrets
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .identity import valid_identity

MAX_BODY = 2 * 1024 * 1024
MAX_EMBED_BODY = 2_200_000
MAX_IMAGE_BYTES = 1_500_000
MAX_CHUNKS = 256
IMAGE_QUERY_INSTRUCTION = "Retrieve relevant images for the query."
TEXT_INSTRUCTION = (
    "Given a question, retrieve relevant text documents that answer the question."
)
logger = logging.getLogger(__name__)


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2000)


class RankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=4000)
    chunks: list[Chunk] = Field(min_length=1, max_length=MAX_CHUNKS)
    limit: int = Field(default=20, ge=1, le=20)
    namespace: str = Field(default="local-experiment", pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    engine: Literal["milvus", "chroma"] = "chroma"
    expected_identity: dict[str, str] | None = None

    @model_validator(mode="after")
    def unique_ids(self) -> "RankRequest":
        if len({chunk.id for chunk in self.chunks}) != len(self.chunks):
            raise ValueError("Duplicate chunk IDs")
        return self


class Ranker:
    def __init__(
        self, encode: Callable, *, cache_size: int = 1024, client=None
    ) -> None:
        if client is None:
            import chromadb
            from chromadb.config import Settings

            client = chromadb.EphemeralClient(Settings(anonymized_telemetry=False))
        self.client = client
        self.encode = encode
        self.cache_size = cache_size
        self.cache: OrderedDict[str, list[float]] = OrderedDict()

    def rank(self, request: RankRequest, space_id: str | None = None) -> list[dict]:
        # The HTTP handler serializes rank() calls to bound GPU concurrency.
        vectors: list[list[float]] = []
        for start in range(0, len(request.chunks), 4):
            batch = request.chunks[start : start + 4]
            keys = [hashlib.sha256(chunk.text.encode()).hexdigest() for chunk in batch]
            missing = {
                key: chunk.text
                for key, chunk in zip(keys, batch, strict=True)
                if key not in self.cache
            }
            if missing:
                encoded = self.encode(list(missing.values()))
                if len(encoded) != len(missing):
                    raise ValueError("Invalid embedding count")
                self.cache.update(zip(missing, encoded, strict=True))
            for key in keys:
                vector = self.cache[key]
                if not vector or not all(math.isfinite(value) for value in vector):
                    raise ValueError("Invalid embedding")
                vectors.append(vector)
                self.cache.move_to_end(key)
            while len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
        query = self.encode(
            [request.query], is_query=True, instruction=TEXT_INSTRUCTION
        )
        if request.engine == "milvus":
            from .milvus_store import rank

            return rank(
                request.namespace,
                [chunk.id for chunk in request.chunks],
                [chunk.text for chunk in request.chunks],
                vectors,
                query,
                request.limit,
                space_id=space_id,
            )
        name = "request-" + uuid.uuid4().hex
        collection = self.client.create_collection(
            name, metadata={"hnsw:space": "cosine"}
        )
        try:
            # Store IDs and embeddings only: no document text, filenames or paths.
            collection.add(
                ids=[chunk.id for chunk in request.chunks], embeddings=vectors
            )
            result = collection.query(
                query_embeddings=query,
                n_results=min(request.limit, len(request.chunks)),
                include=["distances"],
            )
            return [
                {"id": identifier, "score": max(-1.0, min(1.0, 1.0 - distance))}
                for identifier, distance in zip(
                    result["ids"][0], result["distances"][0], strict=True
                )
            ]
        finally:
            self.client.delete_collection(name)


class EmbedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["text", "image"]
    text: str | None = Field(default=None, max_length=1000)
    image_base64: str | None = Field(default=None, max_length=MAX_EMBED_BODY)
    is_query: bool = True
    expected_identity: dict[str, str] | None = None

    @model_validator(mode="after")
    def shape(self) -> "EmbedRequest":
        if self.kind == "text":
            if not self.text or not self.text.strip() or self.image_base64 is not None:
                raise ValueError("text query required")
        elif not self.image_base64 or self.text is not None:
            raise ValueError("image required")
        else:
            _image_bytes(self.image_base64)
        return self


class Embedder:
    """文本和图片都由调用方传入的函数编码，测试不加载真实模型。"""

    def __init__(self, encode_text: Callable, encode_image: Callable) -> None:
        self.encode_text = encode_text
        self.encode_image = encode_image

    def embed(self, request: EmbedRequest) -> list[float]:
        if request.kind == "text":
            vectors = self.encode_text(
                [request.text],
                is_query=request.is_query,
                instruction=IMAGE_QUERY_INSTRUCTION if request.is_query else None,
            )
        else:
            vectors = self.encode_image([_image_bytes(request.image_base64 or "")])
        if len(vectors) != 1:
            raise ValueError("Invalid embedding count")
        vector = vectors[0]
        if not vector or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in vector
        ):
            raise ValueError("Invalid embedding")
        if any(not math.isfinite(float(value)) for value in vector):
            raise ValueError("Invalid embedding")
        return [float(value) for value in vector]


def _image_bytes(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid image") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("invalid image")
    if not (raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")):
        raise ValueError("invalid image")
    return raw


def handler_type(
    ranker: Ranker, token: str, embedder: Embedder | None = None,
    identity: dict[str, str] | None = None,
) -> type[BaseHTTPRequestHandler]:
    if identity is not None and not valid_identity(identity):
        raise ValueError("invalid worker identity")
    inference = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, format: str, *args) -> None:
            # Default logs include paths supplied by clients. Log no request data.
            pass

        def reply(self, status: int, data: dict) -> None:
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authenticated(self) -> bool:
            value = self.headers.get("Authorization", "")
            if not secrets.compare_digest(value.encode(), ("Bearer " + token).encode()):
                self.reply(401, {"code": "unauthorized"})
                return False
            return True

        def do_GET(self) -> None:
            if not self.authenticated():
                return
            if self.path != "/health":
                self.reply(404, {"code": "not_found"})
                return
            if identity is None:
                self.reply(503, {"code": "identity_unavailable"})
                return
            self.reply(200, {"status": "ready", "max_chunks": MAX_CHUNKS, "identity": identity})

        def do_POST(self) -> None:
            if not self.authenticated():
                return
            if self.path not in {"/rank", "/embed"}:
                self.reply(404, {"code": "not_found"})
                return
            if self.headers.get("Transfer-Encoding"):
                self.reply(400, {"code": "invalid_framing"})
                return
            limit = MAX_EMBED_BODY if self.path == "/embed" else MAX_BODY
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            if not 0 < size <= limit:
                self.reply(413, {"code": "body_size"})
                return
            if self.path == "/embed" and embedder is None:
                self.reply(503, {"code": "embedding_unavailable"})
                return
            if identity is None:
                self.reply(503, {"code": "identity_unavailable"})
                return
            if not inference.acquire(blocking=False):
                self.reply(503, {"code": "busy"})
                return
            try:
                body = self.rfile.read(size)
                if self.path == "/embed":
                    request = EmbedRequest.model_validate_json(body)
                    if request.expected_identity is not None and request.expected_identity != identity:
                        self.reply(409, {"code": "identity_mismatch"})
                        return
                    assert embedder is not None
                    self.reply(200, {"vector": embedder.embed(request), "identity": identity})
                    return
                ranked = RankRequest.model_validate_json(body)
                if ranked.expected_identity != identity:
                    self.reply(409, {"code": "identity_mismatch"})
                    return
                self.reply(200, {"hits": ranker.rank(ranked, identity["space_id"]), "identity": identity})
            except ValidationError:
                self.reply(422, {"code": "invalid_input"})
            except Exception as exc:
                logger.error("Ranking failed: %s", type(exc).__name__)
                code = "embedding_failed" if self.path == "/embed" else "ranking_failed"
                self.reply(503, {"code": code})
            finally:
                inference.release()

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.environ.get("ZEBAO_RAG_WORKER_TOKEN", "")
    if len(token) < 32:
        parser.error("ZEBAO_RAG_WORKER_TOKEN must contain at least 32 characters")
    from .embedder import encode_image_bytes, encode_texts, get_verified_identity

    logging.basicConfig(level=logging.INFO)
    # Only advertise readiness after weights are loaded; no sample data is read.
    identity = get_verified_identity(TEXT_INSTRUCTION)
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        handler_type(
            Ranker(encode_texts), token, Embedder(encode_texts, encode_image_bytes), identity
        ),
    )
    server.daemon_threads = True
    logger.info("zebao-rag ready on 127.0.0.1:%s", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
