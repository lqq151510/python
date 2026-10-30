"""Real ephemeral Chroma + deterministic embeddings; no model or user data."""

import base64
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import ValidationError

from zebao_rag import milvus_store
from zebao_rag.worker import Embedder, Ranker, RankRequest, handler_type

IDENTITY = {"space_id": "a" * 64, "protocol_id": "b" * 64, "generation": "c" * 32}

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
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type(self.ranker, token, identity=IDENTITY))
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
                {"query": "cache", "chunks": [{"id": "a", "text": "cache"}], "expected_identity": IDENTITY}
            ).encode()
            with opener.open(
                Request(base + "/rank", data=data, headers=headers)
            ) as response:
                result = json.load(response)
                self.assertEqual(result["hits"][0]["id"], "a")
                self.assertEqual(result["identity"], IDENTITY)
            with opener.open(Request(base + "/health", headers=headers)) as response:
                self.assertEqual(json.load(response)["identity"], IDENTITY)
            missing_identity = json.dumps({"query": "cache", "chunks": [{"id": "a", "text": "cache"}]}).encode()
            with self.assertRaises(HTTPError) as mismatch:
                opener.open(Request(base + "/rank", data=missing_identity, headers=headers))
            self.assertEqual(mismatch.exception.code, 409)
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
            ("127.0.0.1", 0), handler_type(Ranker(encode), token, embedder, IDENTITY)
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
                result = json.load(response)
                self.assertEqual(result["vector"], [0.2, 0.8])
                self.assertEqual(result["identity"], IDENTITY)
            different_identity = dict(IDENTITY, generation="d" * 32)
            data = json.dumps({"kind": "text", "text": "query", "expected_identity": different_identity}).encode()
            with self.assertRaises(HTTPError) as mismatch:
                opener.open(Request(base + "/embed", data=data, headers=headers))
            self.assertEqual(mismatch.exception.code, 409)
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
    def test_same_dimension_model_spaces_do_not_overwrite_each_other(self):
        connection = MagicMock()
        connection.has_collection.return_value = True
        connection.describe_collection.return_value = {"fields": [{"name": "vector", "params": {"dim": 2}}]}
        connection.search.return_value = [[]]
        with patch.object(milvus_store, "client", return_value=connection):
            for space in ("a" * 64, "b" * 64):
                milvus_store.rank("owner", ["a"], ["alpha"], [[1.0, 0.0]], [[1.0, 0.0]], 1, space_id=space)
        keys = [call.kwargs["data"][0]["id"] for call in connection.upsert.call_args_list]
        self.assertNotEqual(keys[0], keys[1])
        connection.drop_collection.assert_not_called()

    def test_dimension_mismatch_never_deletes_or_writes_collection(self):
        connection = MagicMock()
        connection.has_collection.return_value = True
        connection.describe_collection.return_value = {
            "fields": [{"name": "vector", "params": {"dim": 3}}]
        }
        with patch.object(milvus_store, "client", return_value=connection):
            with self.assertRaises(milvus_store.MilvusDimensionMismatch):
                milvus_store.rank("owner", ["a"], ["alpha"], [[1.0, 0.0]], [[1.0, 0.0]], 1, space_id=IDENTITY["space_id"])
        connection.drop_collection.assert_not_called()
        connection.create_collection.assert_not_called()
        connection.upsert.assert_not_called()
        connection.search.assert_not_called()

    def test_dimension_mismatch_becomes_worker_503(self):
        connection = MagicMock()
        connection.has_collection.return_value = True
        connection.describe_collection.return_value = {
            "fields": [{"name": "vector", "params": {"dim": 3}}]
        }
        token = "test-only-token-" * 3
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), handler_type(Ranker(encode, client=object()), token, identity=IDENTITY)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        data = json.dumps({"query": "cache", "chunks": [{"id": "a", "text": "cache"}], "engine": "milvus", "expected_identity": IDENTITY}).encode()
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        try:
            with patch.object(milvus_store, "client", return_value=connection):
                with self.assertRaises(HTTPError) as caught:
                    build_opener(ProxyHandler({})).open(Request(f"http://127.0.0.1:{server.server_port}/rank", data=data, headers=headers))
            self.assertEqual(caught.exception.code, 503)
            self.assertEqual(json.load(caught.exception)["code"], "ranking_failed")
            connection.drop_collection.assert_not_called()
            connection.upsert.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
