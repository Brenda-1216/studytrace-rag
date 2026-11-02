"use strict";

const $ = (id) => document.getElementById(id);
const state = {sessionId: null, busy: false, controller: null, loading: false};
const main = $("main");
let autoscroll = true;
main.addEventListener("scroll", () => {autoscroll = main.scrollHeight - main.scrollTop - main.clientHeight < 100;});

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`); svg.append(use); return svg;
}
function scrollBottom() {if (autoscroll) main.scrollTop = main.scrollHeight;}
function notice(text = "") {$("notice").textContent = text; $("notice").hidden = !text;}
function rememberSession(id) {
  state.sessionId = id;
  try {if (id) localStorage.setItem("zhiye.session", id); else localStorage.removeItem("zhiye.session");} catch (_) {}
}
function syncControls() {
  $("send-button").disabled = state.busy || state.loading || !$("question").value.trim();
  $("stop-button").hidden = !state.busy;
  $("send-button").hidden = state.busy;
  $("new-chat").disabled = state.busy || state.loading;
  $("top-k").disabled = state.busy;
  document.querySelectorAll(".session-select,.delete-session,.suggestion").forEach(b => {b.disabled = state.busy || state.loading;});
}
function closeMenu() {$("sidebar").classList.remove("open"); $("overlay").hidden = true;}
$("menu-button").onclick = () => {const open = $("sidebar").classList.toggle("open"); $("overlay").hidden = !open;};
$("overlay").onclick = closeMenu;

async function jsonRequest(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let reason = `请求失败（${response.status}）`;
    try {const data = await response.json(); if (typeof data.detail === "string") reason = data.detail;} catch (_) {}
    const error = new Error(reason); error.status = response.status; throw error;
  }
  return response.json();
}

function addMessage(role, text = "") {
  $("welcome").hidden = true; $("messages").hidden = false;
  const article = el("article", `message ${role}`);
  const label = el("div", "message-label");
  if (role === "assistant") {const mark = el("span", "assistant-icon"); mark.append(icon("book")); label.append(mark);}
  label.append(el("span", "", role === "user" ? "你" : "知页 · 课程助教"));
  const body = el("div", "message-body streaming", text);
  article.append(label, body); $("messages").append(article);
  scrollBottom();
  return {article, body};
}

function addSources(article, sources, query) {
  const details = el("details", "source-details");
  const summary = el("summary"); summary.append(icon("doc"), el("span", "", `参考讲义 · ${sources.length} 个片段`)); details.append(summary);
  const targets = new Map();
  for (const source of sources) {
    const card = el("div", "source-card");
    card.append(el("div", "source-title", `[${source.id}] ${source.source}`));
    const position = source.page != null ? `第 ${source.page} 页` : `第 ${source.line_start}–${source.line_end} 行`;
    card.append(el("div", "source-position", position), el("p", "source-text", source.text));
    details.append(card); targets.set(Number(source.id), card);
  }
  if (query) details.append(el("div", "retrieval-query", `本轮检索：${query}`));
  if (sources.length || query) article.append(details);
  return (id) => {const card = targets.get(id); if (card) {details.open = true; card.scrollIntoView({behavior:"smooth", block:"center"});}};
}

// 只构建 DOM 文本节点，模型和讲义中的 HTML 不会执行。
function inline(parent, text, sources, jump) {
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[\d+\])/g;
  let position = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(position, match.index)));
    const token = match[0];
    if (token.startsWith("**")) parent.append(el("strong", "", token.slice(2,-2)));
    else if (token.startsWith("`")) parent.append(el("code", "", token.slice(1,-1)));
    else {
      const id = Number(token.slice(1,-1));
      if (sources.some(s => Number(s.id) === id)) {const button = el("button", "citation", token); button.type = "button"; button.title = `查看引用 ${id}`; button.onclick = () => jump(id); parent.append(button);}
      else parent.append(document.createTextNode(token));
    }
    position = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(position)));
}
function renderAnswer(body, text, sources, jump) {
  body.replaceChildren(); body.classList.remove("streaming");
  let code = null, list = null;
  for (const line of text.split("\n")) {
    if (line.trimStart().startsWith("```")) {
      if (code) code = null;
      else {const pre = el("pre"); code = el("code"); pre.append(code); body.append(pre);} list = null; continue;
    }
    if (code) {code.textContent += line + "\n"; continue;}
    if (!line.trim()) {list = null; continue;}
    const bullet = line.match(/^\s*(?:[-*]|\d+[.、])\s+(.+)$/);
    if (bullet) {if (!list) {list = el("ul"); body.append(list);} const li = el("li"); inline(li, bullet[1], sources, jump); list.append(li); continue;}
    list = null;
    const heading = line.match(/^#{1,6}\s+(.+)$/);
    const block = el(heading ? "h3" : "p"); inline(block, heading ? heading[1] : line, sources, jump); body.append(block);
  }
}

async function refreshSessions() {
  const data = await jsonRequest("/sessions");
  $("session-list").replaceChildren(); $("session-count").textContent = data.sessions.length;
  if (!data.sessions.length) {const empty = el("p", "empty-history", "还没有对话\n从一个好问题开始吧"); empty.style.whiteSpace = "pre-line"; $("session-list").append(empty);}
  for (const session of data.sessions) {
    const row = el("div", `session-row${session.session_id === state.sessionId ? " active" : ""}`);
    const select = el("button", "session-select"); select.title = `${session.title} · ${session.turn_count} 轮`; select.append(icon("chat"), el("span", "", session.title));
    select.onclick = () => loadSession(session.session_id);
    const remove = el("button", "delete-session"); remove.append(icon("trash")); remove.setAttribute("aria-label", `删除对话：${session.title}`);
    remove.onclick = async () => {
      if (state.busy || state.loading || !confirm(`删除对话“${session.title}”？此操作会删除本地历史。`)) return;
      state.loading = true; syncControls();
      try {await jsonRequest(`/sessions/${session.session_id}`, {method:"DELETE"}); if (session.session_id === state.sessionId) resetChat(); await refreshSessions();}
      catch (error) {notice(error.message);} finally {state.loading = false; syncControls();}
    };
    row.append(select, remove); $("session-list").append(row);
  }
  syncControls();
}

function resetChat() {
  rememberSession(null); $("messages").replaceChildren(); $("messages").hidden = true;
  $("welcome").hidden = false; notice(); $("question").value = ""; $("question").style.height = "";
  main.scrollTop = 0; autoscroll = true;
  document.querySelectorAll(".session-row").forEach(row => row.classList.remove("active")); closeMenu(); syncControls();
}
$("new-chat").onclick = () => {if (!state.busy && !state.loading) {resetChat(); $("question").focus();}};

async function loadSession(id) {
  if (state.busy || state.loading) return;
  state.loading = true; syncControls(); notice();
  try {
    const data = await jsonRequest(`/sessions/${id}`);
    rememberSession(id); $("messages").replaceChildren(); $("welcome").hidden = data.turns.length > 0; $("messages").hidden = !data.turns.length;
    autoscroll = true;
    for (const turn of data.turns) {addMessage("user", turn.question); const message = addMessage("assistant"); const jump = addSources(message.article, turn.sources); renderAnswer(message.body, turn.answer, turn.sources, jump);}
    scrollBottom(); closeMenu(); await refreshSessions();
  } catch (error) {
    if (error.status === 404) resetChat(); notice(error.message);
  } finally {state.loading = false; syncControls();}
}

async function consumeEvents(response, handler) {
  if (!response.body) throw new Error("当前浏览器不支持流式响应，请使用新版 Edge 或 Chrome。");
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const {value, done} = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, {stream:true});
      let separator;
      while ((separator = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, separator.index); buffer = buffer.slice(separator.index + separator[0].length);
        let name = "message"; const data = [];
        for (const line of block.split(/\r?\n/)) {if (line.startsWith("event:")) name = line.slice(6).trim(); else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());}
        if (data.length) handler(name, JSON.parse(data.join("\n")));
      }
      if (done) break;
    }
  } finally {reader.releaseLock();}
}

async function sendQuestion(question) {
  if (state.busy || state.loading || !question.trim()) return;
  if (question.length > 2000) {notice("问题最多 2000 个字符，请缩短后再发送。"); return;}
  state.busy = true; state.controller = new AbortController(); syncControls(); notice(); closeMenu();
  $("question").value = ""; $("question").style.height = ""; autoscroll = true;
  addMessage("user", question);
  const message = addMessage("assistant"); const loading = el("div", "loading", state.sessionId ? "正在理解追问、检索讲义…" : "正在检索讲义…"); message.body.append(loading); scrollBottom();
  let answer = "", sources = [], completed = false, jump = () => {};
  try {
    const response = await fetch("/ask", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({question, top_k:Number($("top-k").value), session_id:state.sessionId}), signal:state.controller.signal});
    if (!response.ok) {let detail = `请求失败（${response.status}）`; try {const data = await response.json(); if (typeof data.detail === "string") detail = data.detail;} catch (_) {} if (response.status === 404) rememberSession(null); throw new Error(detail);}
    await consumeEvents(response, (name, data) => {
      if (name === "sources") {rememberSession(data.session_id); sources = data.sources; jump = addSources(message.article, sources, data.retrieval_query); loading.textContent = "正在组织回答…";}
      else if (name === "delta") {answer += data.text; message.body.textContent = answer; scrollBottom();}
      else if (name === "error") throw new Error(data.message);
      else if (name === "done") {completed = true; rememberSession(data.session_id);}
    });
    if (!completed) throw new Error("连接提前结束，答案可能不完整。请重新打开该对话核对历史后再重试。");
    renderAnswer(message.body, answer, sources, jump);
    message.article.append(el("div", "message-state", "回答完成 · 可继续追问"));
  } catch (error) {
    if (answer) renderAnswer(message.body, answer, sources, jump); else message.body.replaceChildren();
    const text = error.name === "AbortError" ? "已停止生成。请重新打开该对话核对是否已保存本轮。" : error.message;
    message.article.append(el("div", "message-state failed", text)); notice(text);
    if (!answer && error.name !== "AbortError" && !$("question").value) $("question").value = question;
  } finally {
    state.busy = false; state.controller = null; syncControls(); scrollBottom(); $("question").focus();
    try {await refreshSessions();} catch (_) {notice("无法刷新历史列表，请检查服务是否正在运行。");}
  }
}

$("chat-form").onsubmit = (event) => {event.preventDefault(); sendQuestion($("question").value.trim());};
$("question").addEventListener("input", () => {$("question").style.height = "auto"; $("question").style.height = Math.min($("question").scrollHeight, 160) + "px"; syncControls();});
$("question").addEventListener("keydown", event => {if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {event.preventDefault(); $("chat-form").requestSubmit();}});
$("stop-button").onclick = () => state.controller?.abort();
document.querySelectorAll(".suggestion").forEach(button => {button.onclick = () => sendQuestion(button.dataset.question);});

async function checkHealth() {
  try {
    const health = await jsonRequest("/health");
    $("health-status").classList.toggle("error", !health.index_exists);
    $("health-status").lastElementChild.textContent = health.index_exists ? "知识库已就绪" : "等待导入讲义";
    $("model-label").textContent = `${health.chat_model} · Ollama 本地运行`;
    $("health-status").title = "知识库状态不代表模型连通性；首次回答可能需要加载模型。";
    if (!health.index_exists) notice("请先在终端导入讲义：python -m app.ingest --input data/documents");
  } catch (_) {$("health-status").classList.add("error"); $("health-status").lastElementChild.textContent = "服务未连接"; notice("连接不到服务，请确认 FastAPI 已启动后刷新页面。");}
}

async function init() {
  await checkHealth();
  try {await refreshSessions();} catch (_) {notice("暂时无法读取对话历史，请确认服务已更新并刷新。");}
  let saved = null; try {saved = localStorage.getItem("zhiye.session");} catch (_) {}
  if (saved) await loadSession(saved);
  syncControls();
}
init();
