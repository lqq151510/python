import os
import uuid
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

from app import learning_store, semantic
from tests.api.routes.test_documents import headers, upload
from tests.utils.user import create_random_user

URL_PATH = "/api/v1/learning"


@pytest.fixture
def _journal(monkeypatch):
    admin = None
    name = "devassist_learning_test_" + uuid.uuid4().hex[:10]
    if os.environ.get("VERIFY_MULTIDB") == "1":
        config = dotenv_values(Path(__file__).resolve().parents[2] / ".local/mysql.env")
        url = URL.create(
            "mysql+pymysql",
            username="root",
            password=config["MYSQL_ROOT_PASSWORD"],
            host="127.0.0.1",
            port=3306,
            database="mysql",
            query={"charset": "utf8mb4"},
        )
        admin = create_engine(url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
        database = create_engine(url.set(database=name), hide_parameters=True)
    else:
        database = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    learning_store.Base.metadata.create_all(database)
    monkeypatch.setattr(learning_store, "engine", lambda: database)
    try:
        yield database
    finally:
        database.dispose()
        if admin:
            with admin.connect() as connection:
                connection.execute(text(f"DROP DATABASE `{name}`"))
            admin.dispose()


def test_journal_create_list_delete_and_owner_isolation(
    client: TestClient, db: Session, _journal
) -> None:
    owner, other = create_random_user(db), create_random_user(db)
    assert client.get(URL_PATH).status_code == 401
    response = client.post(
        URL_PATH,
        headers=headers(owner),
        json={"title": "今天学缓存", "notes": "Redis 缓存有有效期。"},
    )
    assert response.status_code == 201
    identifier = response.json()["id"]
    assert (
        client.get(URL_PATH, headers=headers(owner)).json()["data"][0]["notes"]
        == "Redis 缓存有有效期。"
    )
    assert client.get(URL_PATH, headers=headers(other)).json()["data"] == []
    assert (
        client.delete(URL_PATH + "/" + identifier, headers=headers(other)).status_code
        == 404
    )
    assert (
        client.delete(URL_PATH + "/" + identifier, headers=headers(owner)).status_code
        == 200
    )
    assert client.get(URL_PATH, headers=headers(owner)).json()["data"] == []


def test_compare_validates_scope_and_saves_only_current_users_experiment(
    client: TestClient, db: Session, _journal, monkeypatch
) -> None:
    owner, other = create_random_user(db), create_random_user(db)
    own_doc = upload(client, owner, "# Cache\nRedis cache expiry.").json()["document"]
    foreign = upload(client, other, "private-text").json()["document"]
    stores = []

    def rank(_query, _identifiers, texts, _limit, namespace, store, cache_info):
        assert namespace == str(owner.id)
        assert "private-text" not in " ".join(texts)
        stores.append(store)
        cache_info["status"] = "miss"
        return [(0, 0.9)]

    monkeypatch.setattr(semantic, "vector_rank", rank)
    response = client.post(
        URL_PATH + "/compare",
        headers=headers(owner),
        json={"query": "缓存", "document_id": foreign["id"]},
    )
    assert response.status_code == 404 and stores == []
    response = client.post(
        URL_PATH + "/compare",
        headers=headers(owner),
        json={"query": "缓存", "document_id": own_doc["id"]},
    )
    assert response.status_code == 200
    assert stores == ["milvus", "chroma"]
    assert response.json()["result"]["shared_hits"] == 1
    assert "content" not in str(response.json()["result"])
    assert client.get(URL_PATH, headers=headers(other)).json()["data"] == []


@pytest.mark.skipif(
    os.environ.get("VERIFY_MULTIDB") != "1", reason="Opt-in five-database integration"
)
def test_all_five_databases_real_round_trip(
    client: TestClient, db: Session, _journal
) -> None:
    owner = create_random_user(db)
    document = upload(
        client, owner, "# 令牌\n访问令牌过期后，服务端要求用户重新登录。", "tokens.md"
    ).json()["document"]
    # Record only generated test owner IDs so verification can remove its vectors.
    path = Path(__file__).resolve().parents[2] / ".local/zebao-rag/test-owners.txt"
    with path.open("a") as file:
        file.write(str(owner.id) + "\n")
    payload = {"query": "登录凭据失效怎么办", "document_id": document["id"]}
    first = client.post(URL_PATH + "/compare", headers=headers(owner), json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["result"]["shared_hits"] == 1
    for store in ("milvus", "chroma"):
        assert (
            first.json()["result"]["stores"][store]["hits"][0]["filename"]
            == "tokens.md"
        )
        assert first.json()["result"]["stores"][store]["cache_status"] == "miss"
    second = client.post(URL_PATH + "/compare", headers=headers(owner), json=payload)
    assert second.status_code == 200, second.text
    assert all(
        result["cache_status"] == "hit"
        for result in second.json()["result"]["stores"].values()
    )
    stats = client.get(URL_PATH + "/cache/stats", headers=headers(owner)).json()
    assert stats["available"] and stats["hit"] >= 2 and stats["miss"] >= 2
    listing = client.get(URL_PATH, headers=headers(owner)).json()["data"]
    assert len(listing) == 2 and all(
        record["kind"] == "comparison" for record in listing
    )
