import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask

from app.config import Settings
from app.conversations import Conversations, bounded_history
from app.rag import RagService
from app.store import IndexNotReady

logger = logging.getLogger(__name__)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=10)
    session_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


def event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(settings: Settings | None = None, service=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.rag = service if service is not None else RagService(settings)
        app.state.conversations = Conversations(settings.history_db, settings.history_max_turns)
        yield

    app = FastAPI(title="专业课 RAG", version="0.3.0", lifespan=lifespan)
    web_dir = Path(__file__).resolve().parent / "web"
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def chat_page():
        return FileResponse(web_dir / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sessions")
    async def list_sessions():
        return {"sessions": app.state.conversations.list_sessions()}

    def get_history(session_id: UUID):
        try:
            return app.state.conversations.history(str(session_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="会话不存在，请不带 session_id 发起新对话") from exc

    @app.get("/sessions/{session_id}")
    async def session_history(session_id: UUID):
        return {"session_id": str(session_id), "turns": get_history(session_id)}

    @app.delete("/sessions/{session_id}")
    async def delete_session(session_id: UUID):
        conversations = app.state.conversations
        if str(session_id) in conversations.active:
            raise HTTPException(status_code=409, detail="该会话正在回答，请稍后删除")
        get_history(session_id)
        conversations.delete(str(session_id))
        return {"deleted": True}

    @app.get("/health")
    async def health():
        return {"status": "ok", "index_exists": (settings.index_dir / "CURRENT").is_file(),
                "chat_model": settings.chat_model, "embedding_model": settings.embedding_model}

    @app.post("/ask")
    async def ask(body: AskRequest, request: Request):
        rag = request.app.state.rag
        conversations = request.app.state.conversations
        session_id = str(body.session_id) if body.session_id else conversations.create()
        turns = get_history(UUID(session_id))
        if session_id in conversations.active:
            raise HTTPException(status_code=409, detail="该会话正在回答，请等待完成后再追问")
        conversations.active.add(session_id)
        history = bounded_history(turns, settings.max_history_chars)
        try:
            query = await rag.rewrite_question(body.question, history)
            sources = await run_in_threadpool(
                rag.retrieve, query, body.top_k or settings.top_k
            )
        except IndexNotReady as exc:
            conversations.active.discard(session_id)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            conversations.active.discard(session_id)
            logger.exception("检索失败")
            raise HTTPException(status_code=503, detail="问题改写或检索失败，请检查索引、Ollama 服务和模型") from exc
        except BaseException:
            conversations.active.discard(session_id)
            raise

        async def generate():
            try:
                yield event("sources", {"sources": sources, "session_id": session_id,
                                        "retrieval_query": query})
                answer = ""
                if not sources:
                    answer = "当前讲义中没有足够依据。"
                    yield event("delta", {"text": answer})
                else:
                    async for token in rag.stream(body.question, sources, history):
                        if await request.is_disconnected():
                            return
                        answer += token
                        yield event("delta", {"text": token})
                if await request.is_disconnected():
                    return
                if not answer.strip():
                    raise ValueError("模型没有返回答案")
                conversations.append(session_id, body.question, answer, sources)
                yield event("done", {"session_id": session_id})
            except Exception:
                logger.exception("生成答案失败")
                yield event("error", {"message": "生成失败，请检查 Ollama 服务、聊天模型或超时配置"})
            finally:
                conversations.active.discard(session_id)

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 background=BackgroundTask(conversations.active.discard, session_id), headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })

    return app


app = create_app()
