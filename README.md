# 专业课 RAG 问答系统

轻量化的操作系统 / 计组讲义问答后端。PDF / Markdown → 分块 → Ollama 向量化 → FAISS 本地索引 → 检索 → 拼接提示词 → Ollama 流式回答，附带来源原文及位置。

提供独立问答网页、文档导入命令、`GET /health`、`POST /ask` 和会话历史接口，支持本地持久化多轮对话，没有登录。完整范围见 [SPEC.md](SPEC.md)。

## 问答网页

**最方便的启动方式：** 在 PyCharm 左侧找到项目根目录的 `run.py`，右键选择“运行 run”。它会启动服务，准备好后自动用默认浏览器打开问答网页。解释器选用 `.venv\Scripts\python.exe`，不需要编辑运行参数或输入终端命令。

也可以在资源管理器打开项目文件夹，双击 **`启动问答.bat`**。启动后保持运行窗口打开；结束时按 Ctrl+C 或 PyCharm 的红色停止按钮。Ollama 仍需在后台运行。

启动器会优先使用 8001 端口；如果发现附近端口已有本项目网页，会直接打开已有服务，避免重复启动。端口被其他程序占用则自动尝试下一个，实际网址会打印在运行窗口。如果只是打开已有服务，启动器正常结束（退出代码 0）不会停止原服务。

修改 Python 代码后停止并重新运行 `run.py` 即可生效；下面的 `--reload` 命令适合需要自动重载的开发场景。

启动 FastAPI 后，直接访问根地址即可聊天，例如 [http://127.0.0.1:8001/](http://127.0.0.1:8001/)（启动命令需指定 `--port 8001`）。无需 Node、前端构建或额外依赖，也不需要再去 `/docs` 编辑 JSON。

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

- 输入问题，按 Enter 发送，Shift + Enter 换行，回答逐段显示；直接输入下一问即可连续追问。
- 点击“开始新对话”切换到独立会话；左侧可打开或删除历史对话（删除前有确认提示）。窄窗口点击左上角菜单展开历史。
- 展开回答下方“参考讲义”查看文件、页码/行号及原文；答案中有效的引用编号可点击定位原文。
- “停止生成”会中断当前请求；中断的回答不一定保存，重新打开历史可确认。检查后可手动再次提问，页面不会自动重复提交。
- 选择“检索片段”可调整本次检索数量。顶部状态仅显示服务及索引是否存在，不代表 Ollama 模型健康检查。
- 浏览器记录当前会话 ID，刷新后会尝试恢复；问答原文和来源存储在本地 SQLite。侧边栏最多展示最近 100 个有成功问答的会话，每个会话保留的轮数仍由 HISTORY_MAX_TURNS 控制。

已有服务在编辑前启动且没有自动重载时，请先 Ctrl+C 停止，再用上面的命令重新启动；若根地址返回 404，通常是仍在访问旧进程。也可换一个空闲端口，并使用相同端口的网页地址。

## 1. 准备环境

使用 Python 3.11 或 3.12，以及 [Ollama](https://ollama.com/download)。以下命令从项目根目录执行。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Linux / macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

后续命令使用 Windows 路径，Linux / macOS 替换成 `.venv/bin/python` 即可。不必激活环境。

启动 Ollama（桌面版已运行则无需再启动），再下载两个模型：

```powershell
ollama serve
```

在另一个终端执行：

```powershell
ollama pull qwen2.5:3b
ollama pull bge-m3
ollama list
```

默认聊天模型为 [qwen2.5:3b](https://ollama.com/library/qwen2.5:3b)，向量模型为支持多语言的 [bge-m3](https://ollama.com/library/bge-m3)。首次安装依赖和拉取模型需要联网，下载完成后可本地运行；两类模型都需要准备。按机器内存调整聊天模型，并同步修改 `.env` 中的名称。

## 2. 导入讲义

先用项目自带的两份简短讲义体验：

```powershell
.\.venv\Scripts\python.exe -m app.ingest --input examples/lectures
```

导入自己的讲义：将 PDF、`.md` 或 `.markdown` 文件放入 `data/documents/`，支持子目录，然后运行：

```powershell
.\.venv\Scripts\python.exe -m app.ingest --input data/documents
```

Markdown 必须为 UTF-8。PDF 使用 pypdf 按页提取；扫描件需要先 OCR，加密文件需要先解密。纯图片或全空白文件会报错，部分空白页会跳过。公式、复杂排版、表格可能提取不准确，需要检查原文。

**每次命令全量替换当前知识库**，不是追加。需要同时查询示例和自有讲义时，将它们放在同一个输入目录再导入。修改或删除讲义后重新执行命令。向量模型名称改变后也必须重建；同名模型权重更新后需自行重建。

导入完成后显示文件数、片段数。成功时才切换 `data/index/CURRENT`，正在运行的 API 的下一个请求会读到新索引，无需重启。旧快照保留，便于失败恢复；长期使用时可停止服务后清理 CURRENT 没有指向的旧目录。索引只使用 FAISS 文件和 JSON，不加载 pickle。仅使用本机导入生成的索引。

## 3. 启动服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 健康检查：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- 问答网页：[http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- 接口文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

`/health` 是存活检查：`index_exists` 只表示索引指针存在，不检查索引完整性、Ollama 连通性或模型是否下载。服务可以在未导入文档时启动，此时提问返回 409。

## 4. 发起流式问答

PowerShell 请用 `curl.exe`，避免 `curl` 被解析成别名。`-N` 关闭输出缓冲：

```powershell
curl.exe -N -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" --data-binary "@examples/ask.json"
```

Linux / macOS 使用 `curl`。请求 JSON：

```json
{"question":"死锁发生的四个必要条件是什么？如何预防？","top_k":4}
```

响应类型是 `text/event-stream`，示意如下，具体检索位置和生成内容以实际输出为准：

```text
event: sources
data: {"session_id":"ccf542d5-c63b-4328-ad35-98a11add8cfb","retrieval_query":"死锁发生的四个必要条件是什么？如何预防？","sources":[{"id":1,"source":"os.md","line_start":6,"line_end":10,"chunk_id":"os.md::0::100","text":"死锁发生的四个必要条件是……","distance":0.42}]}

event: delta
data: {"text":"死锁发生需要满足四个必要条件"}

event: delta
data: {"text":"：互斥、请求并保持、不可剥夺、循环等待。[1]"}

event: done
data: {"session_id":"ccf542d5-c63b-4328-ad35-98a11add8cfb"}

```

每个 SSE 事件由空行分隔，`data` 是一整行 JSON，文本中的换行会转义。先接收 `sources`，再顺序拼接 `delta.text`，收到 `done` 代表成功结束；收到 `error` 表示失败，后面不会再有 `done`。连接在 done/error 前中断时应视为不完整答案。

`[1]` 对应 `sources` 中 `id=1` 的片段。PDF 用从 1 开始的 `page` 定位，Markdown 用 `line_start` / `line_end` 定位；`source` 相对于本次导入目录。`text` 是实际送入提示词的原文（可能因上下文预算截断），`distance` 是归一化向量的平方 L2 距离，越小越近，**不是置信度**。

也可使用自带 Python 客户端，按来源和流式文本展示：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe examples/client.py
```

这是 POST 流式接口，浏览器原生 `EventSource` 不能直接发 POST；自建客户端应使用 fetch 的响应流或 httpx。Swagger 页面适合查看 schema，实时输出建议使用 curl 或上述客户端。

## 5. 多轮对话

首轮不传 `session_id`（或者传 `null`），服务自动创建会话。复制 SSE `sources` 或 `done` 里的真实 `session_id`，后续请求携带同一个 ID 即可追问。在 `/docs` 页面修改 Request body，再次 Execute：

```json
{"question":"那怎样预防它？","top_k":2,"session_id":"粘贴上一次返回的真实 UUID"}
```

上例中的占位文字必须替换为实际返回值，否则返回 422。每轮等待 done/error 后再发送下一轮。想换一个独立话题，移除 `session_id` 或设成 `null` 即可开始新会话。

后续轮次会先让聊天模型结合历史消解代词，生成独立检索问题（例如“那怎样预防它？”改写为“怎样预防死锁？”），再进行向量检索和流式回答。`retrieval_query` 返回实际检索问题，方便排查。首轮无需额外改写调用，后续轮次会多一次模型请求，首个事件返回前等待时间会更长。模型可能改写失误，可在下一轮明确写出主题。

也可以使用自动维护会话 ID 的终端聊天客户端：

```powershell
.\.venv\Scripts\python.exe examples/chat.py --url http://127.0.0.1:8001
```

输入问题后回车，回答结束后继续输入追问。`/new` 新开会话，`/exit` 退出；`--session 真实UUID` 可恢复之前的会话。客户端使用 httpx（包含在开发依赖中）。服务端口不是 8001 时修改 `--url`。

- `GET /sessions/{session_id}`：查看最近保留的完整问答及各轮来源。
- `GET /sessions`：按最新完成问答排序，返回最近 100 个有历史的会话及首条保留问题作为标题。
- `DELETE /sessions/{session_id}`：删除会话和历史，删除后旧 ID 返回 404。
- 默认 SQLite 保存在 `data/conversations.sqlite3`，API 重启后仍可用；重载期间进行中的回答会中断。
- 每个会话最多保存最近 10 轮，发送给模型的历史最多 4000 字符（从最新轮向前选取，预算边界处截断回答，舍弃更旧轮次）；数据库中的单轮原文不因提示词预算而截断。历史用于理解追问，回答依据仍为本轮检索片段，引用编号每轮重新分配。
- 生成失败、空回答或检测到断连时不保存本轮；若网络恰好在提交后中断，可能已保存但未收到 done，请通过 GET 历史确认后再重试。客户端重复发送不是幂等操作，会增加一轮。
- 同一会话正在回答时再次提问/删除会返回 409，不同会话可以并行。本地版本请保持 **单个 Uvicorn 进程（不要使用多个 workers 或同时启动多个服务共用同一历史数据库）**，会话忙碌状态在进程内管理。
- 本地演示未实现身份验证，知道会话 ID 的调用方即可查看或删除该会话，不适合直接开放公网。

## 配置

`.env` 或同名环境变量生效，环境变量优先；相对路径以启动命令的工作目录为准。配置修改后重启 API。

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| OLLAMA_BASE_URL | http://localhost:11434 | Ollama 地址 |
| CHAT_MODEL | qwen2.5:3b | 回答模型 |
| EMBEDDING_MODEL | bge-m3 | 导入和查询使用同一向量模型 |
| INDEX_DIR | data/index | 持久化索引目录 |
| CHUNK_SIZE | 800 | 每块最大字符数，不是 token 数 |
| CHUNK_OVERLAP | 120 | 相邻块重叠上限，必须小于块大小 |
| TOP_K | 4 | 默认检索数量，请求可指定 1–10 |
| MAX_CONTEXT_CHARS | 6000 | 送入模型的片段正文总字符上限 |
| OLLAMA_TIMEOUT | 120 | Ollama HTTP 超时秒数 |
| HISTORY_DB | data/conversations.sqlite3 | SQLite 历史数据库 |
| HISTORY_MAX_TURNS | 10 | 每个会话保留的最近完整轮数 |
| MAX_HISTORY_CHARS | 4000 | 送入模型的历史问答正文字符上限 |

问题去除首尾空白后不能为空，请求原始长度最多 2000 字符。聊天模型固定温度 0、上下文窗口 8192 token、最大生成 1024 token；字符预算只是粗略约束，遇到上下文过长可降低 MAX_CONTEXT_CHARS。

## 依赖与代码结构

运行依赖全部在 `requirements.txt`：FastAPI、Uvicorn、LangChain Core / Community / Ollama / Text Splitters、FAISS CPU、pypdf、pydantic-settings。开发依赖另加 pytest 和 httpx。`requirements-lock.txt` 若存在则是本次测试环境的精确版本，可用 `pip install -r requirements-lock.txt` 复现（Python 3.12）。

```text
app/config.py       环境变量及参数校验
app/documents.py    PDF / Markdown 读取、分块、来源追踪
app/store.py        Ollama embedding、FAISS 快照读写
app/ingest.py       命令行全量导入
app/rag.py          Top-K 检索、提示词、异步生成
app/conversations.py SQLite 会话存储、轮数及历史字符限制
app/main.py         健康检查、POST SSE 接口
app/web/            独立问答网页，原生 HTML / CSS / JavaScript
examples/lectures/  两份可直接导入的教学示例
examples/client.py  流式问答客户端
examples/chat.py    自动维护会话 ID 的多轮终端聊天
tests/             不依赖真实 Ollama 的自动化测试
```

## 测试与故障排查

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

自动化测试使用确定性测试向量和模拟回答，真实执行 PDF 解析、Markdown 分块、FAISS 持久化检索以及 HTTP/SSE 协议。其中一项通过本地 HTTP 桩调用真实 LangChain Ollama 客户端，检查 embedding 请求、上下文拼接及流式解析。它们不评估模型回答质量；真实联调请按上面的下载、导入、提问三步执行，并检查引用能否对应原文。

自动化测试另外覆盖会话持久化、隔离、删除、历史预算、失败重试、同会话并发拒绝，以及问题改写和历史消息传递。安装的 `langchain-community` 会给出维护状态弃用警告；精确依赖记录于锁定文件，后续升级时应重新验证。

网页版本验证：Python 3.12 下 18 项测试通过，JavaScript 语法检查通过；新增测试覆盖静态页面和会话列表的过滤、排序、删除。本机 Ollama 已完成多轮生成及历史保存验证。这些样例不代表所有追问或引用都能正确处理。

| 现象 | 处理 |
| --- | --- |
| 422 | 检查空问题、问题长度、top_k 范围 |
| 409 | 先导入文档；模型配置不匹配时重建索引 |
| 503 | 查看服务日志，确认索引可读、Ollama 已启动且 embedding 模型已下载 |
| SSE error | 确认聊天模型已下载；首次加载较慢时调整超时 |
| 导入失败 | 检查 UTF-8、PDF 是否加密/扫描件、Ollama 连通性及模型名称 |
| 输出慢或卡住 | 首次加载模型需等待；检查内存和 Ollama 日志，尝试更小的聊天模型 |

## 学习与边界

可以沿 `documents.py → store.py → rag.py → main.py` 阅读完整 RAG 链路。检索只做稠密向量 Top-K，没有相关性阈值或重排，因此不相关问题也可能返回片段；依据不足的拒答依靠提示词，不能保证杜绝幻觉。来源列表表示送入模型的检索依据，不等于模型每条引用都已被语义校验。建议人工核对重要结论。

适合小型课程讲义：导入与索引加载均使用内存，每次查询读取当前完整快照，不适合大规模高并发。多轮历史只保留有限轮次，没有自动摘要或长期记忆。网页使用安全 DOM 文本节点呈现回答，支持基础标题、列表、加粗和代码，复杂 Markdown 表格/公式暂不渲染。没有 OCR、上传 API 或复杂权限，默认仅监听本机。

实现参考 [LangChain ChatOllama 文档](https://docs.langchain.com/oss/python/integrations/chat/ollama) 和 [OllamaEmbeddings 文档](https://docs.langchain.com/oss/python/integrations/embeddings/ollama)。
