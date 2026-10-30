"""Milvus application index. Search is restricted to the current SQL snapshot."""

import hashlib
import json
import os
from functools import lru_cache


class MilvusDimensionMismatch(Exception):
    """The existing collection belongs to a different embedding dimension."""


@lru_cache(maxsize=1)
def client():
    from pymilvus import MilvusClient

    return MilvusClient(
        uri=os.environ.get("ZEBAO_MILVUS_URL", "http://127.0.0.1:19531"), timeout=10
    )


def rank(namespace, identifiers, texts, vectors, query, limit, *, space_id=None):
    if not isinstance(space_id, str) or len(space_id) != 64 or any(
        char not in "0123456789abcdef" for char in space_id
    ):
        raise ValueError("verified embedding identity required")
    connection = client()
    name = os.environ.get("ZEBAO_MILVUS_COLLECTION", "devassist_text_v1")
    dimension = len(vectors[0])
    collection_exists = connection.has_collection(name, timeout=5)
    if collection_exists:
        schema = connection.describe_collection(name, timeout=5)
        vector_field = next(
            field for field in schema["fields"] if field["name"] == "vector"
        )
        if int(vector_field["params"]["dim"]) != dimension:
            # Never delete a shared collection in response to a query.
            raise MilvusDimensionMismatch("Milvus vector dimension mismatch")
    if not collection_exists:
        connection.create_collection(
            name,
            dimension=dimension,
            primary_field_name="id",
            id_type="string",
            max_length=64,
            metric_type="COSINE",
            consistency_level="Strong",
            timeout=15,
        )
    keys = [
        hashlib.sha256(json.dumps([space_id, namespace, identifier, text], ensure_ascii=False).encode()).hexdigest()
        for identifier, text in zip(identifiers, texts, strict=True)
    ]
    connection.upsert(
        name,
        data=[
            {"id": key, "owner_id": namespace, "chunk_id": identifier, "vector": vector}
            for key, identifier, vector in zip(keys, identifiers, vectors, strict=True)
        ],
        timeout=15,
    )
    result = connection.search(
        name,
        data=query,
        filter=f"owner_id == {json.dumps(namespace)} and id in {json.dumps(keys)}",
        limit=min(limit, len(keys)),
        output_fields=["chunk_id"],
        search_params={"metric_type": "COSINE", "params": {}},
        consistency_level="Strong",
        timeout=15,
    )
    allowed = dict(zip(keys, identifiers, strict=True))
    return [
        {"id": allowed[hit["id"]], "score": max(-1.0, min(1.0, hit["distance"]))}
        for hit in result[0]
    ]
