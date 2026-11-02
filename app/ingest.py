import argparse
import json
import sys
from pathlib import Path

from app.config import Settings
from app.documents import load_chunks
from app.store import build_index, make_embeddings


def main() -> int:
    parser = argparse.ArgumentParser(description="全量重建 PDF / Markdown 知识库")
    parser.add_argument("--input", type=Path, default=Path("data/documents"))
    args = parser.parse_args()
    try:
        settings = Settings()
        chunks = load_chunks(args.input, settings)
        print(f"读取完成，共 {len(chunks)} 个片段，开始向量化……", flush=True)
        stats = build_index(chunks, settings, make_embeddings(settings))
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"导入失败（原有索引保持可用）：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
