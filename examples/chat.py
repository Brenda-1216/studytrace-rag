"""连续多轮聊天：python examples/chat.py --url http://127.0.0.1:8001"""
import argparse
import json

import httpx


def main():
    parser = argparse.ArgumentParser(description="专业课 RAG 多轮聊天客户端")
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--session", help="继续已有会话 ID")
    args = parser.parse_args()
    session_id = args.session
    print("输入问题开始聊天；/new 新会话，/exit 退出。")
    while True:
        try:
            question = input("\n你：").strip()
            if question == "/exit":
                break
            if question == "/new":
                session_id = None
                print("已切换到新会话，旧历史仍保留。")
                continue
            if not question:
                continue
            payload = {"question": question}
            if session_id:
                payload["session_id"] = session_id
            with httpx.stream("POST", f"{args.url.rstrip('/')}/ask", timeout=300, json=payload) as response:
                response.raise_for_status()
                event_name = ""
                ended = False
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event_name = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        if event_name == "sources":
                            session_id = data["session_id"]
                            print(f"会话：{session_id}\n检索问题：{data['retrieval_query']}")
                            for source in data["sources"]:
                                position = (f"第 {source['page']} 页" if "page" in source else
                                            f"第 {source['line_start']}-{source['line_end']} 行")
                                print(f"[{source['id']}] {source['source']} {position}")
                            print("助教：", end="", flush=True)
                        elif event_name == "delta":
                            print(data["text"], end="", flush=True)
                        elif event_name == "error":
                            ended = True
                            print(f"\n错误：{data['message']}（本轮未保存）")
                        elif event_name == "done":
                            ended = True
                            print()
                if not ended:
                    print("\n连接提前结束，请查看会话历史确认是否保存。")
        except (EOFError, KeyboardInterrupt):
            break
        except httpx.HTTPError as exc:
            print(f"请求失败：{exc}")


if __name__ == "__main__":
    main()
