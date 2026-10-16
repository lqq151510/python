"""Real ephemeral Chroma + deterministic embeddings; no model or user data."""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import ValidationError
from zebao_rag.worker import Ranker, RankRequest, handler_type


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


if __name__ == "__main__":
    unittest.main()
