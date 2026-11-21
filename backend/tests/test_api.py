def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "mode": "demo"}


def test_create_and_reload_history(client):
    response = client.post(
        "/api/messages",
        json={"text": "  Нужна загрузка документов  ", "mode": "analyst"},
    )
    assert response.status_code == 201
    turn = response.json()
    assert turn["user"]["content"] == "Нужна загрузка документов"
    assert turn["assistant"]["analysis"]["is_demo"] is True
    assert turn["assistant"]["analysis"]["requirements"][0]["id"] == "FR-01"

    history = client.get("/api/messages").json()
    assert [msg["role"] for msg in history] == ["user", "assistant"]
    assert history[1]["analysis"]["is_demo"] is True


def test_modes(client):
    for mode, section in [("critic", "issues"), ("qa", "acceptance_criteria")]:
        response = client.post("/api/messages", json={"text": "Проверь требования", "mode": mode})
        assert response.status_code == 201
        assert response.json()["assistant"]["analysis"][section]


def test_invalid_messages_are_rejected(client):
    for text in ["   ", "", "x" * 501]:
        assert client.post("/api/messages", json={"text": text}).status_code == 422
    assert client.post("/api/messages", json={"text": "ok", "mode": "other"}).status_code == 422
    assert client.get("/api/messages").json() == []


def test_clear_history(client):
    assert client.post("/api/messages", json={"text": "Hello"}).status_code == 201
    assert client.delete("/api/messages").status_code == 204
    assert client.get("/api/messages").json() == []
