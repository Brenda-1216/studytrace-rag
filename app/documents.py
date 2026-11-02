"""按页读取 PDF、按字符位置追踪 Markdown；保留可展示的引用位置。"""
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from app.config import Settings


def load_chunks(root: Path, settings: Settings) -> list[Document]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"文档目录不存在：{root}")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        add_start_index=True,
        separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
    )
    chunks = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".md", ".markdown"}:
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"文档链接超出导入目录：{path}")
        source = path.relative_to(root).as_posix()
        try:
            if path.suffix.lower() == ".pdf":
                reader = PdfReader(path)
                if reader.is_encrypted:
                    raise ValueError("不支持加密 PDF，请先解密")
                pages = [(page.extract_text() or "", {"page": i})
                         for i, page in enumerate(reader.pages, start=1)]
            else:
                pages = [(path.read_text(encoding="utf-8-sig"), {})]
            file_chunks = []
            for text, location in pages:
                for chunk in splitter.create_documents([text], [{"source": source, **location}]):
                    start = chunk.metadata.pop("start_index")
                    if "page" not in location:
                        chunk.metadata["line_start"] = text.count("\n", 0, start) + 1
                        chunk.metadata["line_end"] = text.count(
                            "\n", 0, start + len(chunk.page_content)
                        ) + 1
                    chunk.metadata["chunk_id"] = f"{source}::{location.get('page', 0)}::{start}"
                    file_chunks.append(chunk)
            if not file_chunks:
                raise ValueError("没有可提取的文字；扫描件需先 OCR")
            chunks.extend(file_chunks)
        except Exception as exc:
            raise ValueError(f"读取 {source} 失败：{exc}") from exc
    if not chunks:
        raise ValueError("目录中没有可导入的 PDF / Markdown 文档")
    return chunks
