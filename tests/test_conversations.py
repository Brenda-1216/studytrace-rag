import asyncio
from threading import Event, Thread

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from app.config import Settings
from app.conversations import Conversations, bounded_history
from app.main import create_app
from app.rag import RagService
from test_rag import FakeRag, parse_events


def settings(tmp_path, **kwargs):
    return Settings(index_dir=tmp_path, history_db=tmp_path / "history.sqlite3", **kwargs)


def test_multiturn_persistence_isolation_and_delete(tmp_path):
    class TrackingRag(FakeRag):
        def __init__(self):
            self.histories = []
            self.queries = []

        async def rewrite_question(self, question, history):
            self.histories.append(history)
            return "死锁如何预防？" if history else question

        def retrieve(self, question, top_k):
            self.queries.append(question)
            return super().retrieve(question, top_k)

    rag = TrackingRag()
    config = settings(tmp_path)
    with TestClient(create_app(config, rag)) as client:
        first = parse_events(client.post("/ask", json={"question": "什么是死锁？"}))
        sid = first[0][1]["session_id"]
        assert first[-1][1]["session_id"] == sid
        second = parse_events(client.post("/ask", json={"question": "如何预防它？", "session_id": sid}))
        assert second[0][1]["retrieval_query"] == "死锁如何预防？"
        assert rag.queries[-1] == "死锁如何预防？"
        assert rag.histories[-1][0]["question"] == "什么是死锁？"
        assert "四个条件" in rag.histories[-1][0]["answer"]
        fresh = parse_events(client.post("/ask", json={"question": "Cache 是什么？"}))
        assert fresh[0][1]["session_id"] != sid
        assert rag.histories[-1] == []
    with TestClient(create_app(config, rag)) as client:
        turns = client.get(f"/sessions/{sid}").json()["turns"]
        assert len(turns) == 2
        assert turns[0]["sources"][0]["source"] == "os.md"
        assert client.delete(f"/sessions/{sid}").status_code == 200
        assert client.get(f"/sessions/{sid}").status_code == 404
        assert client.post("/ask", json={"question": "继续", "session_id": sid}).status_code == 404
        assert client.post("/ask", json={"question": "继续", "session_id": "wrong"}).status_code == 422


def test_history_limits(tmp_path):
    store = Conversations(tmp_path / "db.sqlite3", max_turns=2)
    sid = store.create()
    for i in range(3):
        store.append(sid, f"问题{i}", "答案" * 100, [])
    history = store.history(sid)
    assert [t["question"] for t in history] == ["问题1", "问题2"]
    bounded = bounded_history(history, 100)
    assert sum(len(t["question"]) + len(t["answer"]) for t in bounded) <= 100
    assert bounded[-1]["question"] == "问题2"
    assert len(history[-1]["answer"]) == 200


def test_failed_generation_does_not_save_and_can_retry(tmp_path):
    class BrokenRag(FakeRag):
        async def stream(self, question, sources, history=None):
            yield "不完整"
            raise RuntimeError("offline")

    app = create_app(settings(tmp_path), BrokenRag())
    with TestClient(app) as client:
        events = parse_events(client.post("/ask", json={"question": "死锁"}))
        sid = events[0][1]["session_id"]
        assert events[-1][0] == "error"
        assert client.get(f"/sessions/{sid}").json()["turns"] == []
        app.state.rag = FakeRag()
        assert client.post("/ask", json={"question": "重试", "session_id": sid}).status_code == 200
        assert len(client.get(f"/sessions/{sid}").json()["turns"]) == 1


def test_same_session_concurrency_rejected(tmp_path):
    entered, release = Event(), Event()

    class WaitingRag(FakeRag):
        async def stream(self, question, sources, history=None):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            yield "完成。[1]"

    app = create_app(settings(tmp_path), WaitingRag())
    with TestClient(app) as client:
        sid = app.state.conversations.create()
        worker = Thread(target=lambda: client.post("/ask", json={"question": "死锁", "session_id": sid}))
        worker.start()
        try:
            assert entered.wait(timeout=3)
            assert client.post("/ask", json={"question": "追问", "session_id": sid}).status_code == 409
            assert client.delete(f"/sessions/{sid}").status_code == 409
        finally:
            release.set()
            worker.join(timeout=3)
        assert not worker.is_alive()
        assert len(client.get(f"/sessions/{sid}").json()["turns"]) == 1


def test_rewrite_and_answer_use_history():
    class LLM:
        def __init__(self):
            self.requests = []

        async def ainvoke(self, messages, **kwargs):
            self.requests.append(messages)
            assert kwargs["format"]["required"] == ["query"]
            return AIMessage(content='{"query":"死锁如何预防？"}')

        async def astream(self, messages):
            self.requests.append(messages)
            yield AIMessageChunk(content="破坏循环等待。[1]")

    service = RagService.__new__(RagService)
    service.llm = LLM()
    history = [{"question": "什么是死锁", "answer": "互相等待。[9]"}]

    async def run():
        assert await service.rewrite_question("什么是 Cache", []) == "什么是 Cache"
        assert service.llm.requests == []
        assert await service.rewrite_question("如何预防它", history) == "死锁如何预防？"
        tokens = [t async for t in service.stream("如何预防它", FakeRag().retrieve("", 1), history)]
        assert tokens == ["破坏循环等待。[1]"]
    asyncio.run(run())
    assert "什么是死锁" in service.llm.requests[0][1].content
    assert service.llm.requests[1][1].content == "什么是死锁"
    assert service.llm.requests[1][2].content == "互相等待。"
    assert "死锁的条件" in service.llm.requests[-1][-1].content


def test_disconnect_discards_partial_answer(tmp_path):
    from app.main import AskRequest
    app = create_app(settings(tmp_path), FakeRag())

    class DisconnectedRequest:
        async def is_disconnected(self):
            return True

    request = DisconnectedRequest()
    request.app = app
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/ask")
    with TestClient(app):
        async def run():
            response = await endpoint(AskRequest(question="死锁"), request)
            iterator = response.body_iterator
            first = await anext(iterator)
            import json
            sid = json.loads(first.split("\n")[1][6:])["session_id"]
            rest = [event async for event in iterator]
            assert rest == []
            assert app.state.conversations.history(sid) == []
            assert sid not in app.state.conversations.active
        asyncio.run(run())
