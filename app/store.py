"""FAISS + JSON 快照，避免使用 pickle。CURRENT 原子切换后新请求自动加载。"""
import json
import os
from pathlib import Path
from uuid import uuid4

import faiss
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings

from app.config import Settings


class IndexNotReady(ValueError):
    pass


def make_embeddings(settings: Settings) -> OllamaEmbeddings:
    return OllamaEmbeddings(
        model=settings.embedding_model, base_url=settings.ollama_base_url,
        client_kwargs={"timeout": settings.ollama_timeout},
    )


def build_index(chunks: list[Document], settings: Settings, embeddings: Embeddings) -> dict:
    store = FAISS.from_documents(chunks, embeddings, normalize_L2=True)
    root = settings.index_dir
    root.mkdir(parents=True, exist_ok=True)
    generation = uuid4().hex
    target = root / generation
    target.mkdir()
    faiss.write_index(store.index, str(target / "index.faiss"))
    records = []
    for i in range(store.index.ntotal):
        doc = store.docstore.search(store.index_to_docstore_id[i])
        records.append({"page_content": doc.page_content, "metadata": doc.metadata})
    manifest = {"schema": 1, "embedding_model": settings.embedding_model,
                "chunks": len(records), "files": len({d.metadata["source"] for d in chunks}),
                "chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap,
                "documents": records}
    (target / "documents.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    pointer = root / f".CURRENT-{generation}"
    pointer.write_text(generation, encoding="ascii")
    os.replace(pointer, root / "CURRENT")
    return {k: v for k, v in manifest.items() if k != "documents"}


class IndexRepository:
    def __init__(self, settings: Settings, embeddings: Embeddings):
        self.settings = settings
        self.embeddings = embeddings

    def load(self) -> FAISS:
        # 每次读取一个完整快照；不共享可变缓存，也不在请求期间修改索引。
        try:
            generation = (self.settings.index_dir / "CURRENT").read_text(encoding="ascii").strip()
        except FileNotFoundError as exc:
            raise IndexNotReady("知识库未导入，请先运行 python -m app.ingest --input data/documents") from exc
        if len(generation) != 32 or any(c not in "0123456789abcdef" for c in generation):
            raise IndexNotReady("索引指针无效，请重新导入")
        target = self.settings.index_dir / generation
        manifest = json.loads((target / "documents.json").read_text(encoding="utf-8"))
        if manifest["schema"] != 1 or manifest["embedding_model"] != self.settings.embedding_model:
            raise IndexNotReady("索引版本或向量模型不一致，请重新导入")
        index = faiss.read_index(str(target / "index.faiss"))
        docs = {str(i): Document(**doc) for i, doc in enumerate(manifest["documents"])}
        if index.ntotal != len(docs):
            raise IndexNotReady("索引与文档数量不一致，请重新导入")
        return FAISS(self.embeddings, index, InMemoryDocstore(docs),
                     {i: str(i) for i in range(len(docs))}, normalize_L2=True)
