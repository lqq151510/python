"""Real ephemeral Chroma + deterministic embeddings; no model or user data."""

import base64
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import ValidationError
from zebao_rag import milvus_store
from zebao_rag.worker import Embedder, Ranker, RankRequest, handler_type


def encode(texts, **kwargs):
    return [[1.0, 0.0] if "cache" in text else [0.0, 1.0] for text in texts]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.ranker = Ranker(encode)

    def test_isolated_candidates_and_no_persistent_source_data(self):
        baseline = len(self.ranker.client.list_collections())
        first = RankRequest(
            query="cache", chunks=[{"id": "owner-a", "text": "cache secret-a"}]
        )
        second = RankRequest(
            query="cache", chunks=[{"id": "owner-b", "text": "unrelated secret-b"}]
        )
        self.assertEqual(self.ranker.rank(first)[0]["id"], "owner-a")
        self.assertEqual(self.ranker.rank(second)[0]["id"], "owner-b")
        self.assertEqual(len(self.ranker.client.list_collections()), baseline)
        self.assertTrue(all(len(key) == 64 for key in self.ranker.cache))

    def test_cache_is_bounded(self):
        self.ranker.cache_size = 2
        request = RankRequest(
            query="cache",
            chunks=[{"id": str(i), "text": f"cache {i}"} for i in range(8)],
        )
        self.ranker.rank(request)
        self.assertLessEqual(len(self.ranker.cache), 2)

    def test_duplicate_ids_and_extra_parameters_are_rejected(self):
        with self.assertRaises(ValidationError):
            RankRequest(
                query="q",
                chunks=[{"id": "x", "text": "one"}, {"id": "x", "text": "two"}],
            )
        with self.assertRaises(ValidationError):
            RankRequest(
                query="q", chunks=[{"id": "x", "text": "one"}], collection="personal"
            )

    def test_http_authentication_validation_and_ranking(self):
        token = "test-only-token-" * 3
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type(self.ranker, token))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        opener = build_opener(ProxyHandler({}))
        try:
            with self.assertRaises(HTTPError) as caught:
                opener.open(base + "/health")
            self.assertEqual(caught.exception.code, 401)
            headers = {
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            }
            data = json.dumps(
                {"query": "cache", "chunks": [{"id": "a", "text": "cache"}]}
            ).encode()
            with opener.open(
                Request(base + "/rank", data=data, headers=headers)
            ) as response:
                self.assertEqual(json.load(response)["hits"][0]["id"], "a")
            with self.assertRaises(HTTPError) as caught:
                opener.open(Request(base + "/rank", data=b"{}", headers=headers))
            self.assertEqual(caught.exception.code, 422)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class EmbedTests(unittest.TestCase):
    def test_embed_stays_in_memory_and_rejects_bad_images(self):
        def encode_text(_texts, **kwargs):
            self.assertEqual(
                kwargs.get("instruction"), "Retrieve relevant images for the query."
            )
            return [[0.2, 0.8]]

        def encode_image(payloads):
            self.assertTrue(payloads[0].startswith(b"\xff\xd8\xff"))
            return [[0.9, 0.1]]

        token = "test-only-token-" * 3
        embedder = Embedder(encode_text, encode_image)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), handler_type(Ranker(encode), token, embedder)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        opener = build_opener(ProxyHandler({}))
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        }
        try:
            with self.assertRaises(HTTPError) as caught:
                opener.open(base + "/embed")
            self.assertEqual(caught.exception.code, 401)
            data = json.dumps(
                {"kind": "text", "text": "药品包装", "is_query": True}
            ).encode()
            with opener.open(
                Request(base + "/embed", data=data, headers=headers)
            ) as response:
                self.assertEqual(json.load(response)["vector"], [0.2, 0.8])
            image = json.dumps(
                {
                    "kind": "image",
                    "image_base64": base64.b64encode(b"\xff\xd8\xfffake").decode(),
                    "path": "/tmp/secret.jpg",
                }
            ).encode()
            with self.assertRaises(HTTPError) as caught:
                opener.open(Request(base + "/embed", data=image, headers=headers))
            self.assertEqual(caught.exception.code, 422)
            bad = json.dumps(
                {
                    "kind": "image",
                    "image_base64": base64.b64encode(b"not-an-image").decode(),
                }
            ).encode()
            with self.assertRaises(HTTPError) as caught:
                opener.open(Request(base + "/embed", data=bad, headers=headers))
            self.assertEqual(caught.exception.code, 422)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class MilvusStoreTests(unittest.TestCase):
    def test_embedding_dimension_change_rebuilds_derived_index(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "ZEBAO_MILVUS_URL": os.path.join(directory, "index.db"),
                "ZEBAO_MILVUS_COLLECTION": "test_dimension_change",
            }
            with patch.dict(os.environ, environment):
                milvus_store.client.cache_clear()
                try:
                    first = milvus_store.rank(
                        "test-owner",
                        ["a"],
                        ["alpha"],
                        [[1.0, 0.0]],
                        [[1.0, 0.0]],
                        1,
                    )
                    self.assertEqual(first, [{"id": "a", "score": 1.0}])
                    second = milvus_store.rank(
                        "test-owner",
                        ["a"],
                        ["alpha"],
                        [[1.0, 0.0, 0.0]],
                        [[1.0, 0.0, 0.0]],
                        1,
                    )
                    self.assertEqual(second, [{"id": "a", "score": 1.0}])
                finally:
                    milvus_store.client().close()
                    milvus_store.client.cache_clear()


if __name__ == "__main__":
    unittest.main()
