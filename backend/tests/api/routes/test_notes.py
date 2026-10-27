from fastapi.testclient import TestClient

from app.core.config import settings


def test_note_is_private_to_its_owner(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
) -> None:
    created = client.post(
        f"{settings.API_V1_STR}/notes",
        headers=normal_user_token_headers,
        json={"title": "检索阈值", "body": "无命中时不调用模型。"},
    )
    assert created.status_code == 201
    note_id = created.json()["id"]

    listed = client.get(
        f"{settings.API_V1_STR}/notes",
        headers=normal_user_token_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["data"][0]["title"] == "检索阈值"

    hidden = client.patch(
        f"{settings.API_V1_STR}/notes/{note_id}",
        headers=superuser_token_headers,
        json={"title": "不该改到别人的笔记"},
    )
    assert hidden.status_code == 404

    updated = client.patch(
        f"{settings.API_V1_STR}/notes/{note_id}",
        headers=normal_user_token_headers,
        json={"body": "无命中时直接说明资料不足。"},
    )
    assert updated.status_code == 200
    assert "资料不足" in updated.json()["body"]

    removed = client.delete(
        f"{settings.API_V1_STR}/notes/{note_id}",
        headers=normal_user_token_headers,
    )
    assert removed.status_code == 204


def test_blank_note_is_rejected(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/notes",
        headers=normal_user_token_headers,
        json={"title": "   ", "body": "有内容"},
    )
    assert response.status_code == 422
