"""Milvus application index. Search is restricted to the current SQL snapshot."""

import hashlib
import json
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def client():
    from pymilvus import MilvusClient

    return MilvusClient(
        uri=os.environ.get("ZEBAO_MILVUS_URL", "http://127.0.0.1:19531"), timeout=10
    )


def rank(namespace, identifiers, texts, vectors, query, limit):
    connection = client()
    name = os.environ.get("ZEBAO_MILVUS_COLLECTION", "devassist_text_v1")
    if not connection.has_collection(name, timeout=5):
        connection.create_collection(
            name,
            dimension=len(vectors[0]),
            primary_field_name="id",
            id_type="string",
            max_length=64,
            metric_type="COSINE",
            consistency_level="Strong",
            timeout=15,
        )
    keys = [
        hashlib.sha256(f"{namespace}:{identifier}:{text}".encode()).hexdigest()
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
