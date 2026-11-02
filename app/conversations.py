"""SQLite 保存完整问答轮次；生成失败或断连时不写入半条回答。"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class Conversations:
    def __init__(self, path: Path, max_turns: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.max_turns = max_turns
        # 单进程内禁止同一会话交错生成；不同会话可同时进行。
        self.active: set[str] = set()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY)")
            db.execute("""CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
                question TEXT NOT NULL, answer TEXT NOT NULL, sources TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self) -> str:
        session_id = str(uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?)", (session_id,))
        return session_id

    def history(self, session_id: str) -> list[dict]:
        with self.connect() as db:
            if db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is None:
                raise KeyError(session_id)
            rows = db.execute("SELECT question, answer, sources FROM turns WHERE session_id=? ORDER BY id",
                              (session_id,)).fetchall()
        return [{"question": q, "answer": a, "sources": json.loads(s)} for q, a, s in rows]

    def list_sessions(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT s.id,
                (SELECT question FROM turns WHERE session_id=s.id ORDER BY id LIMIT 1),
                COUNT(t.id) FROM sessions s JOIN turns t ON t.session_id=s.id
                GROUP BY s.id ORDER BY MAX(t.id) DESC LIMIT 100""").fetchall()
        return [{"session_id": sid, "title": question[:50], "turn_count": count}
                for sid, question, count in rows]

    def append(self, session_id: str, question: str, answer: str, sources: list[dict]):
        with self.connect() as db:
            db.execute("INSERT INTO turns (session_id, question, answer, sources) VALUES (?, ?, ?, ?)",
                       (session_id, question, answer, json.dumps(sources, ensure_ascii=False)))
            db.execute("""DELETE FROM turns WHERE session_id=? AND id NOT IN
                (SELECT id FROM turns WHERE session_id=? ORDER BY id DESC LIMIT ?)""",
                       (session_id, session_id, self.max_turns))

    def delete(self, session_id: str):
        with self.connect() as db:
            if db.execute("DELETE FROM sessions WHERE id=?", (session_id,)).rowcount == 0:
                raise KeyError(session_id)


def bounded_history(turns: list[dict], budget: int) -> list[dict]:
    """保留最近轮次，按字符限制送入模型的历史；不修改数据库原文。"""
    result = []
    for turn in reversed(turns):
        question = turn["question"]
        if len(question) >= budget:
            break
        answer = turn["answer"][:budget - len(question)]
        result.append({"question": question, "answer": answer})
        budget -= len(question) + len(answer)
        if budget <= 0:
            break
    return list(reversed(result))
