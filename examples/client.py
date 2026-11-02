"""python examples/client.py；先启动服务。"""
import json

import httpx

with httpx.stream("POST", "http://127.0.0.1:8000/ask", timeout=180,
                  json={"question": "死锁发生的四个必要条件是什么？"}) as response:
    response.raise_for_status()
    event_name = ""
    for line in response.iter_lines():
        if line.startswith("event: "):
            event_name = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            if event_name == "sources":
                for source in data["sources"]:
                    position = (f"第 {source['page']} 页" if "page" in source else
                                f"第 {source['line_start']}-{source['line_end']} 行")
                    print(f"[{source['id']}] {source['source']} {position}")
                print()
            elif event_name == "delta":
                print(data["text"], end="", flush=True)
            elif event_name == "error":
                raise SystemExit(f"\n{data['message']}")
            elif event_name == "done":
                print()
