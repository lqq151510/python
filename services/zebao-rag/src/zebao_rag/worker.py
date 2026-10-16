"""Loopback-only semantic ranking for DevAssist's already authorized chunks.

PostgreSQL remains the source of truth. Each request uses a temporary Chroma
collection containing only its supplied candidates; no private corpus is read.
Only normalized document embeddings are cached in bounded process memory.
"""

import argparse
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

MAX_BODY = 2 * 1024 * 1024
MAX_CHUNKS = 256
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

    def rank(self, request: RankRequest) -> list[dict]:
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


def handler_type(ranker: Ranker, token: str) -> type[BaseHTTPRequestHandler]:
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
            self.reply(200, {"status": "ready", "max_chunks": MAX_CHUNKS})

        def do_POST(self) -> None:
            if not self.authenticated():
                return
            if self.path != "/rank":
                self.reply(404, {"code": "not_found"})
                return
            if self.headers.get("Transfer-Encoding"):
                self.reply(400, {"code": "invalid_framing"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            if not 0 < size <= MAX_BODY:
                self.reply(413, {"code": "body_size"})
                return
            if not inference.acquire(blocking=False):
                self.reply(503, {"code": "busy"})
                return
            try:
                body = self.rfile.read(size)
                request = RankRequest.model_validate_json(body)
                self.reply(200, {"hits": ranker.rank(request)})
            except ValidationError:
                self.reply(422, {"code": "invalid_input"})
            except Exception as exc:
                logger.error("Ranking failed: %s", type(exc).__name__)
                self.reply(503, {"code": "ranking_failed"})
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
    from .embedder import encode_texts, get_model

    logging.basicConfig(level=logging.INFO)
    # Only advertise readiness after weights are loaded; no sample data is read.
    get_model()
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), handler_type(Ranker(encode_texts), token)
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
