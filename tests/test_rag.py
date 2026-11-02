import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from pypdf import PdfWriter

from app.config import Settings
from app.documents import load_chunks
from app.main import create_app
from app.rag import RagService
from app.store import IndexNotReady, IndexRepository, build_index


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setenv("HISTORY_DB", str(tmp_path / "history.sqlite3"))


class KeywordEmbeddings(Embeddings):
    """确定性测试向量，不请求真实模型。"""
    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [float("死锁" in text), float("Cache" in text), 0.1]


def test_markdown_locations_and_overlap(tmp_path):
    text = "# 死锁\n\n" + "死锁需要互斥、请求并保持、不可剥夺、循环等待。\n" * 20
    (tmp_path / "os.md").write_text(text, encoding="utf-8")
    chunks = load_chunks(tmp_path, Settings(chunk_size=100, chunk_overlap=20))
    assert len(chunks) > 1
    assert all(len(c.page_content) <= 100 for c in chunks)
    lines = text.splitlines()
    for chunk in chunks:
        m = chunk.metadata
        assert chunk.page_content in "\n".join(lines[m["line_start"] - 1:m["line_end"]])
        assert m["source"] == "os.md"


def test_blank_pdf_rejected(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.write(tmp_path / "scan.pdf")
    with pytest.raises(ValueError, match="OCR"):
        load_chunks(tmp_path, Settings())


def test_pdf_page_reference(tmp_path):
    # 最小可提取 PDF：测试真实 pypdf 解析与 1-based 页码。
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    for text in ("Deadlock", "Cache"):
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 200 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(tmp_path / "notes.pdf")
    chunks = load_chunks(tmp_path, Settings())
    assert [c.metadata["page"] for c in chunks] == [1, 2]
    assert "Cache" in chunks[1].page_content


def test_faiss_snapshot_retrieval_and_rebuild(tmp_path):
    settings = Settings(index_dir=tmp_path)
    embedding = KeywordEmbeddings()
    chunks = [Document(page_content=t, metadata={"source": f"{i}.md"})
              for i, t in enumerate(["死锁的必要条件", "Cache 的局部性"])]
    stats = build_index(chunks, settings, embedding)
    assert stats["chunks"] == 2
    repo = IndexRepository(settings, embedding)
    assert repo.load().similarity_search("死锁", k=1)[0].page_content == "死锁的必要条件"
    build_index(chunks[1:], settings, embedding)
    assert repo.load().index.ntotal == 1  # 全量重建不会重复累计旧片段
    with pytest.raises(IndexNotReady, match="模型"):
        IndexRepository(Settings(index_dir=tmp_path, embedding_model="other"), embedding).load()


def test_failed_rebuild_preserves_pointer(tmp_path):
    settings = Settings(index_dir=tmp_path)
    chunks = [Document(page_content="死锁", metadata={"source": "os.md"})]
    build_index(chunks, settings, KeywordEmbeddings())
    pointer = (tmp_path / "CURRENT").read_text()

    class BrokenEmbeddings(KeywordEmbeddings):
        def embed_documents(self, texts):
            raise RuntimeError("offline")

    with pytest.raises(RuntimeError):
        build_index(chunks, settings, BrokenEmbeddings())
    assert (tmp_path / "CURRENT").read_text() == pointer


def test_context_budget_and_reference_ids(tmp_path):
    settings = Settings(index_dir=tmp_path, max_context_chars=100)
    build_index([Document(page_content="死锁" * 80, metadata={"source": "os.md"})],
                settings, KeywordEmbeddings())
    service = RagService.__new__(RagService)
    service.settings = settings
    service.repository = IndexRepository(settings, KeywordEmbeddings())
    sources = service.retrieve("死锁", 4)
    assert sources[0]["id"] == 1
    assert len(sources[0]["text"]) == 100


class FakeRag:
    async def rewrite_question(self, question, history):
        return question

    def retrieve(self, question, top_k):
        return [{"id": 1, "source": "os.md", "line_start": 1, "line_end": 2,
                 "text": "死锁的条件", "distance": 0.0}]

    async def stream(self, question, sources, history=None):
        yield "死锁"
        yield "需要四个条件。[1]"


def parse_events(response):
    result = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n")
        result.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return result


def test_api_stream_and_validation(tmp_path):
    with TestClient(create_app(Settings(index_dir=tmp_path), FakeRag())) as client:
        assert client.get("/health").json()["index_exists"] is False
        for question in ("", "   ", "x" * 2001):
            assert client.post("/ask", json={"question": question}).status_code == 422
        assert client.post("/ask", json={"question": "死锁", "top_k": 0}).status_code == 422
        response = client.post("/ask", json={"question": "死锁"})
        assert response.headers["content-type"].startswith("text/event-stream")
        events = parse_events(response)
        assert [e[0] for e in events] == ["sources", "delta", "delta", "done"]
        assert events[0][1]["sources"][0]["source"] == "os.md"


def test_missing_index(tmp_path):
    with TestClient(create_app(Settings(index_dir=tmp_path))) as client:
        assert client.post("/ask", json={"question": "死锁"}).status_code == 409


def test_generation_error_and_empty_retrieval(tmp_path):
    class BrokenRag(FakeRag):
        async def stream(self, question, sources, history=None):
            yield "部分答案"
            raise RuntimeError("offline")

    with TestClient(create_app(Settings(index_dir=tmp_path), BrokenRag())) as client:
        events = parse_events(client.post("/ask", json={"question": "死锁"}))
        assert events[-1][0] == "error"
        assert "done" not in [e[0] for e in events]

    class EmptyRag(FakeRag):
        def retrieve(self, question, top_k):
            return []

    with TestClient(create_app(Settings(index_dir=tmp_path), EmptyRag())) as client:
        events = parse_events(client.post("/ask", json={"question": "死锁"}))
        assert events[1][1]["text"] == "当前讲义中没有足够依据。"


def test_retrieval_error(tmp_path):
    class BrokenRag(FakeRag):
        def retrieve(self, question, top_k):
            raise RuntimeError("offline")

    with TestClient(create_app(Settings(index_dir=tmp_path), BrokenRag())) as client:
        assert client.post("/ask", json={"question": "死锁"}).status_code == 503


def test_ollama_http_pipeline(tmp_path):
    """通过本地 HTTP 桩验证真实 LangChain Ollama 客户端的请求和流解析。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from app.store import make_embeddings

    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson" if self.path == "/api/chat"
                             else "application/json")
            self.end_headers()
            if self.path == "/api/embed":
                texts = body["input"]
                texts = [texts] if isinstance(texts, str) else texts
                payload = {"model": body["model"], "embeddings":
                           KeywordEmbeddings().embed_documents(texts)}
                self.wfile.write(json.dumps(payload).encode())
            elif self.path == "/api/chat":
                for content, done in [("四个必要条件。[1]", False), ("", True)]:
                    payload = {"model": body["model"], "message":
                               {"role": "assistant", "content": content}, "done": done}
                    self.wfile.write((json.dumps(payload) + "\n").encode())
                    self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = Settings(index_dir=tmp_path / "index",
                            ollama_base_url=f"http://127.0.0.1:{server.server_port}")
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "os.md").write_text("死锁需要互斥、请求并保持、不可剥夺、循环等待。", encoding="utf-8")
        build_index(load_chunks(docs, settings), settings, make_embeddings(settings))
        with TestClient(create_app(settings)) as client:
            events = parse_events(client.post("/ask", json={"question": "死锁的条件？"}))
        assert [e[0] for e in events] == ["sources", "delta", "done"]
        assert events[1][1]["text"] == "四个必要条件。[1]"
        chat_request = next(body for path, body in received if path == "/api/chat")
        assert "循环等待" in chat_request["messages"][1]["content"]
        assert "[1]" in chat_request["messages"][1]["content"]
        assert chat_request["stream"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
