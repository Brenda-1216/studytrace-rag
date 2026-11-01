"""在 PyCharm 中右键运行此文件，自动启动并打开问答网页。"""
import argparse
import os
from pathlib import Path
import socket
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser


ROOT = Path(__file__).resolve().parent


def running_page(port: int) -> str | None:
    url = f"http://127.0.0.1:{port}/"
    try:
        # 本地探测不经过系统代理，也不调用模型。
        with build_opener(ProxyHandler({})).open(url, timeout=0.3) as response:
            content = response.read(16000).decode("utf-8", errors="replace")
            if response.status == 200 and "<title>知页 · 专业课知识问答</title>" in content:
                return url
    except (URLError, OSError):
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="一键启动知页问答网页")
    parser.add_argument("--port", type=int, default=8001, help="首选端口，默认 8001")
    parser.add_argument("--no-browser", action="store_true", help="只启动，不自动打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65525:
        parser.error("端口必须在 1–65525 之间")
    os.chdir(ROOT)
    ports = list(range(args.port, args.port + 10))
    if args.port == 8001:
        ports.append(8000)
    for port in ports:
        url = running_page(port)
        if url:
            print(f"问答服务已经在运行，直接打开：{url}", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            return 0

    try:
        import uvicorn
    except ImportError:
        print("缺少依赖，请选用项目的 .venv 解释器，或按 README 安装 requirements.txt。")
        return 1

    # 把已绑定的 socket 交给 Uvicorn，避免先探测再启动之间的端口竞争。
    sock = None
    for port in ports:
        candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            candidate.bind(("127.0.0.1", port))
            candidate.listen(128)
            sock = candidate
            break
        except OSError:
            candidate.close()
    if sock is None:
        print("附近端口均被占用，请关闭旧服务，或使用 --port 指定其他端口。")
        return 1

    url = f"http://127.0.0.1:{port}/"
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=port))
    finished = threading.Event()

    def open_when_ready():
        deadline = time.monotonic() + 60
        while not finished.wait(0.1):
            if server.started:
                print(f"\n问答网页已就绪：{url}\n保持此运行窗口打开。停止服务可按 Ctrl+C 或 PyCharm 红色停止按钮。\n", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return
            if time.monotonic() > deadline:
                print("启动时间较长，请查看下方日志；启动成功后可手动打开：" + url, flush=True)
                return

    opener = threading.Thread(target=open_when_ready, daemon=True)
    opener.start()
    try:
        server.run(sockets=[sock])
    finally:
        finished.set()
        opener.join(timeout=1)
        sock.close()
    return 0 if server.started else 1


if __name__ == "__main__":
    raise SystemExit(main())
