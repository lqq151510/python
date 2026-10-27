from fastapi.testclient import TestClient

from app.core.config import settings


def test_study_task_stays_with_its_owner(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
) -> None:
    created = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=normal_user_token_headers,
        json={"title": "复习检索降级", "detail": "无命中时不调用模型"},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert created.json()["done"] is False

    listed = client.get(
        f"{settings.API_V1_STR}/tasks",
        headers=normal_user_token_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["open_count"] == 1

    hidden = client.patch(
        f"{settings.API_V1_STR}/tasks/{task_id}",
        headers=superuser_token_headers,
        json={"done": True},
    )
    assert hidden.status_code == 404

    finished = client.patch(
        f"{settings.API_V1_STR}/tasks/{task_id}",
        headers=normal_user_token_headers,
        json={"done": True},
    )
    assert finished.status_code == 200
    assert finished.json()["done"] is True

    removed = client.delete(
        f"{settings.API_V1_STR}/tasks/{task_id}",
        headers=normal_user_token_headers,
    )
    assert removed.status_code == 204


def test_blank_study_task_is_rejected(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=normal_user_token_headers,
        json={"title": "   ", "detail": ""},
    )
    assert response.status_code == 422
