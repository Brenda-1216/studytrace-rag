from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from test_rag import FakeRag


def test_web_routes_and_session_catalog(tmp_path):
    app = create_app(Settings(history_db=tmp_path / "history.sqlite3"), FakeRag())
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "/static/chat.js" in response.text
        assert client.get("/static/chat.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/sessions").json() == {"sessions": []}
        store = app.state.conversations
        empty = store.create()
        first, second = store.create(), store.create()
        store.append(first, "第一个主题", "回答", [])
        store.append(second, "第二个主题", "回答", [])
        catalog = client.get("/sessions").json()["sessions"]
        assert [s["session_id"] for s in catalog] == [second, first]
        assert empty not in [s["session_id"] for s in catalog]
        store.append(first, "继续追问", "回答", [])
        catalog = client.get("/sessions").json()["sessions"]
        assert catalog[0] == {"session_id": first, "title": "第一个主题", "turn_count": 2}
        client.delete(f"/sessions/{first}")
        assert [s["session_id"] for s in client.get("/sessions").json()["sessions"]] == [second]
