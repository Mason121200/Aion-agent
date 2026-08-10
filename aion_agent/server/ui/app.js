/* Aion Agent Web UI —— 极简三视图：对话 / 学习 / 设置 */
"use strict";

const state = {
  sessionId: null,
  userId: "chat_user",
  sending: false,
  health: null,
  activeTab: "chat",
  abortCtrl: null,
  sessions: [],
  attachments: [],
};

const els = {
  chat: document.getElementById("chat"),
  input: document.getElementById("input"),
  send: document.getElementById("btn-send"),
  attach: document.getElementById("btn-attach"),
  fileInput: document.getElementById("file-input"),
  attachments: document.getElementById("attachments"),
  model: document.getElementById("model-info"),
  sessionTitle: document.getElementById("session-title"),
  configBanner: document.getElementById("config-banner"),
  notifyBanner: document.getElementById("notify-banner"),
  toast: document.getElementById("toast"),
  viewChat: document.getElementById("view-chat"),
  viewStudy: document.getElementById("view-study"),
  viewSettings: document.getElementById("view-settings"),
  studyBody: document.getElementById("study-body"),
  studySub: document.getElementById("study-sub"),
  settingsBody: document.getElementById("settings-body"),
  tabbar: document.getElementById("tabbar"),
  sheet: document.getElementById("session-sheet"),
  sheetMask: document.getElementById("sheet-mask"),
  sessionList: document.getElementById("session-list"),
};

/* ---------- 通用 ---------- */

function toast(text, ms = 2600) {
  els.toast.textContent = text;
  els.toast.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => els.toast.classList.remove("show"), ms);
}

async function api(path, options = {}) {
  const isForm = typeof FormData !== "undefined" && options.body instanceof FormData;
  const res = await fetch(path, {
    headers: isForm ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

/* ---------- 图片渲染（失败回退 URL） ---------- */

function imgFail(el) {
  const src = el && el.getAttribute ? (el.getAttribute("src") || "") : "";
  const a = document.createElement("a");
  a.className = "img-fallback";
  a.href = src;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = "🖼 图片加载失败，点击查看原图：" + (src.length > 80 ? src.slice(0, 80) + "…" : src);
  if (el && el.replaceWith) el.replaceWith(a);
}
window.imgFail = imgFail;

function imgHtml(url) {
  return `<img class="md-img" src="${esc(url)}" alt="图片" loading="lazy" referrerpolicy="no-referrer" onerror="imgFail(this)">`;
}

/* ---------- 时间格式化 / 日期分隔 ---------- */

let lastMsgDate = "";

function _pad2(n) { return String(n).padStart(2, "0"); }
function _fmtDate(d) { return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`; }
function _fmtTime(d) { return `${_pad2(d.getHours())}:${_pad2(d.getMinutes())}`; }

function parseMsgTime(iso) {
  if (iso) {
    const d = new Date(iso);
    if (!isNaN(d.getTime())) return d;
  }
  return new Date();
}

function msgTimeText(iso) {
  const d = parseMsgTime(iso);
  return _fmtDate(d) === _fmtDate(new Date())
    ? _fmtTime(d)
    : _fmtDate(d).slice(5) + " " + _fmtTime(d);
}

/* ---------- 轻量 Markdown 渲染（先转义，再格式化，安全） ---------- */

function mdInline(text) {
  return text
    .replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)\]]+)\)/g, (m, alt, u) => imgHtml(u))
    .replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)\]]+)\)/g,
      (m, t, u) => `<a href="${u.replace(/"/g, "&quot;")}" target="_blank" rel="noopener">${t}</a>`);
}

function renderMarkdown(src) {
  const lines = esc(String(src == null ? "" : src)).split("\n");
  let html = "";
  let list = null;
  let inCode = false;
  let codeBuf = [];
  let para = [];
  const flushPara = () => {
    if (para.length) {
      html += `<p>${mdInline(para.join("<br>"))}</p>`;
      para = [];
    }
  };
  const closeList = () => {
    if (list) { html += `</${list}>`; list = null; }
  };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (/^```/.test(line)) {
      if (!inCode) {
        flushPara(); closeList();
        inCode = true; codeBuf = [];
      } else {
        html += `<pre class="md-pre"><code>${codeBuf.join("\n")}</code></pre>`;
        inCode = false;
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }
    if (!line.trim()) { flushPara(); closeList(); continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      flushPara(); closeList();
      html += `<h${h[1].length}>${mdInline(h[2])}</h${h[1].length}>`;
      continue;
    }
    const ul = line.match(/^[-*]\s+(.*)$/);
    const ol = line.match(/^\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      flushPara();
      const type = ul ? "ul" : "ol";
      if (list !== type) { closeList(); html += `<${type}>`; list = type; }
      html += `<li>${mdInline(ul ? ul[1] : ol[1])}</li>`;
      continue;
    }
    closeList();
    const q = line.match(/^>\s?(.*)$/);
    if (q) { flushPara(); html += `<blockquote>${mdInline(q[1])}</blockquote>`; continue; }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) { flushPara(); html += "<hr>"; continue; }
    para.push(line);
  }
  flushPara(); closeList();
  if (inCode && codeBuf.length) html += `<pre class="md-pre"><code>${codeBuf.join("\n")}</code></pre>`;
  return html || src;
}

function renderMessageBody(el, text) {
  const body = el.querySelector(".md-body");
  if (body) body.innerHTML = renderMarkdown(text);
}

/* ---------- Tab 路由 ---------- */

function switchTab(tab) {
  state.activeTab = tab;
  els.viewChat.classList.toggle("active", tab === "chat");
  els.viewStudy.classList.toggle("active", tab === "study");
  els.viewSettings.classList.toggle("active", tab === "settings");
  for (const btn of els.tabbar.querySelectorAll(".tab")) {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  }
  if (tab === "study") refreshStudy();
  if (tab === "settings") renderSettings();
}

/* ---------- 会话管理 ---------- */

function sessionLabel(s) {
  const t = (s.title || "").trim();
  if (t) return t;
  const preview = (s.preview || "").replace(/\s+/g, " ");
  if (preview) return preview.slice(0, 20);
  return "空会话";
}

function openSheet() {
  els.sheet.classList.remove("hidden");
  els.sheetMask.classList.remove("hidden");
  renderSessionList();
}
function closeSheet() {
  els.sheet.classList.add("hidden");
  els.sheetMask.classList.add("hidden");
}

async function loadSessions() {
  try {
    const data = await api("/api/sessions?user_id=" + encodeURIComponent(state.userId));
    state.sessions = data.sessions || [];
  } catch (_) {
    state.sessions = [];
  }
  const cur = state.sessions.find((s) => s.session_id === state.sessionId);
  els.sessionTitle.textContent = state.sessionId && cur ? sessionLabel(cur) : "新对话";
  return state.sessions;
}

function renderSessionList() {
  const list = state.sessions;
  if (!list.length) {
    els.sessionList.innerHTML = '<div class="empty">还没有会话，点「新会话」开始</div>';
    return;
  }
  els.sessionList.innerHTML = list.map((s) => {
    const active = s.session_id === state.sessionId ? " active" : "";
    const pinned = s.pinned ? " pinned" : "";
    return `<div class="session-item${active}${pinned}" data-sid="${esc(s.session_id)}">
      <div class="session-main">
        <div class="session-name" title="双击重命名">${esc(sessionLabel(s))}</div>
        <div class="session-meta">${s.message_count || 0} 条消息 · ${esc(String(s.created_at || "").slice(5, 16).replace("T", " "))}${s.pinned ? " · 已置顶" : ""}</div>
      </div>
      <button class="del-btn" data-op="del" data-sid="${esc(s.session_id)}" title="删除会话">🗑</button>
      <div class="pin-float"><button class="pin-btn" data-op="pin" data-sid="${esc(s.session_id)}">${s.pinned ? "取消置顶" : "置顶"}</button></div>
    </div>`;
  }).join("");
  els.sessionList.querySelectorAll("[data-op]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const sid = btn.dataset.sid;
      if (btn.dataset.op === "pin") togglePin(sid);
      if (btn.dataset.op === "del") deleteSession(sid);
    });
  });
  els.sessionList.querySelectorAll(".session-item").forEach((item) => {
    item.addEventListener("click", () => {
      clearTimeout(item._selTimer);
      item._selTimer = setTimeout(() => selectSession(item.dataset.sid), 250);
    });
    const name = item.querySelector(".session-name");
    if (name) {
      name.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        clearTimeout(item._selTimer);
        startRename(item, item.dataset.sid);
      });
    }
  });
}

function startRename(item, sid) {
  const s = state.sessions.find((x) => x.session_id === sid);
  if (!s) return;
  const nameEl = item.querySelector(".session-name");
  if (!nameEl) return;
  const input = document.createElement("input");
  input.className = "rename-input";
  input.value = s.title || "";
  input.maxLength = 40;
  nameEl.replaceWith(input);
  input.focus();
  input.select();
  let finished = false;
  const commit = (save) => {
    if (finished) return;
    finished = true;
    const value = input.value.trim();
    const shouldSave = save && value && value !== (s.title || "");
    input.blur();
    (async () => {
      if (shouldSave) {
        try {
          await api("/api/session/" + encodeURIComponent(sid) + "/meta", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: value }),
          });
        } catch (e) {
          toast("重命名失败: " + e.message);
        }
      }
      await loadSessions();
      renderSessionList();
    })();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit(true);
    if (e.key === "Escape") commit(false);
  });
  input.addEventListener("blur", () => commit(true));
}

async function togglePin(sid) {
  const s = state.sessions.find((x) => x.session_id === sid);
  if (!s) return;
  try {
    await api("/api/session/" + encodeURIComponent(sid) + "/meta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned: !s.pinned }),
    });
    await loadSessions();
    renderSessionList();
  } catch (e) {
    toast("操作失败: " + e.message);
  }
}

async function deleteSession(sid) {
  if (!confirm("确定删除这个会话吗？历史记录将清除。")) return;
  try {
    await api("/api/session/" + encodeURIComponent(sid), { method: "DELETE" });
  } catch (e) {
    toast("删除失败: " + e.message);
    return;
  }
  await loadSessions();
  if (state.sessionId === sid) {
    if (state.sessions.length) {
      state.sessionId = state.sessions[0].session_id;
      els.chat.innerHTML = "";
      lastMsgDate = "";
      await loadHistory();
      await loadSessions();
    } else {
      state.sessionId = null;
      els.chat.innerHTML = "";
      lastMsgDate = "";
      maybeShowWelcome();
    }
    closeSheet();
    switchTab("chat");
  }
  renderSessionList();
  refreshMemory();
}

async function newSession() {
  try {
    const data = await api("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: state.userId }),
    });
    state.sessionId = data.session_id;
  } catch (e) {
    toast("新建失败: " + e.message);
    return;
  }
  els.chat.innerHTML = "";
  lastMsgDate = "";
  clearAttachments();
  await loadSessions();
  closeSheet();
  switchTab("chat");
  refreshMemory();
  maybeShowWelcome();
}

async function selectSession(sid) {
  if (!sid || sid === state.sessionId) return;
  state.sessionId = sid;
  els.chat.innerHTML = "";
  lastMsgDate = "";
  clearAttachments();
  await loadHistory();
  await loadSessions();
  closeSheet();
  switchTab("chat");
  refreshMemory();
}

/* ---------- 聊天渲染 ---------- */

function addMessage(role, text, iso, images) {
  const d = parseMsgTime(iso);
  const day = _fmtDate(d);
  if (lastMsgDate && lastMsgDate !== day) {
    const div = document.createElement("div");
    div.className = "day-divider";
    div.innerHTML = `<span>${day}</span>`;
    els.chat.appendChild(div);
  }
  lastMsgDate = day;
  const el = document.createElement("div");
  el.className = "msg " + role;
  const body = document.createElement("div");
  body.className = "md-body";
  body.textContent = text;
  el.appendChild(body);
  if (images && images.length) {
    const imgBox = document.createElement("div");
    imgBox.className = "msg-imgs";
    imgBox.innerHTML = images.map(imgHtml).join("");
    el.appendChild(imgBox);
  }
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = msgTimeText(iso);
  el.appendChild(meta);
  els.chat.appendChild(el);
  renderMessageBody(el, text);
  scrollBottom();
  return el;
}

function appendGeneratedImage(url) {
  const bubble = currentAssistant();
  if (!bubble) return;
  let imgs = bubble.querySelector(".msg-imgs");
  if (!imgs) {
    imgs = document.createElement("div");
    imgs.className = "msg-imgs";
    const meta = bubble.querySelector(".msg-meta");
    bubble.insertBefore(imgs, meta);
  }
  imgs.insertAdjacentHTML("beforeend", imgHtml(url));
  scrollBottom();
}

function scrollBottom() {
  els.chat.scrollTop = els.chat.scrollHeight;
}

function currentAssistant() {
  const nodes = els.chat.querySelectorAll(".msg.assistant");
  return nodes.length ? nodes[nodes.length - 1] : null;
}

function addChip(text) {
  const bubble = currentAssistant();
  if (!bubble) return;
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.textContent = text;
  bubble.appendChild(chip);
  scrollBottom();
}

function renderHistory(messages) {
  lastMsgDate = "";
  for (const m of messages) {
    if (m.role === "user" || m.role === "assistant") {
      addMessage(m.role, m.content, m.created_at, m.images);
    }
  }
}

/* ---------- 附件上传 ---------- */

function renderAttachments() {
  els.attachments.innerHTML = "";
  if (!state.attachments.length) {
    els.attachments.classList.add("hidden");
    updateSendState();
    return;
  }
  els.attachments.classList.remove("hidden");
  state.attachments.forEach((a, i) => {
    const thumb = document.createElement("div");
    thumb.className = "thumb";
    thumb.innerHTML = `<img src="${esc(a.url)}" alt="${esc(a.name || "图片")}"><span class="rm" title="移除">×</span>`;
    thumb.querySelector(".rm").addEventListener("click", () => {
      state.attachments.splice(i, 1);
      renderAttachments();
    });
    els.attachments.appendChild(thumb);
  });
  updateSendState();
}

function clearAttachments() {
  state.attachments = [];
  renderAttachments();
}

function maybeShowWelcome() {
  const hasMsg = els.chat.querySelector(".msg");
  if (hasMsg || !state.sessionId) return;
  const w = document.createElement("div");
  w.className = "welcome";
  w.innerHTML = `<div class="welcome-logo">✦</div>
    <h2>Aion Agent</h2>
    <p>有长期记忆的 AI 助手。告诉我你的名字、目标或学习计划，我会记住并在以后的对话里自然想起。</p>
    <div class="welcome-tips">试试：「帮我制定一个三个月学英语的计划」<br>「我晚上效率最高」</div>`;
  els.chat.appendChild(w);
  scrollBottom();
}

async function initSession() {
  const data = await api("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: state.userId }),
  });
  state.sessionId = data.session_id;
  return data.session_id;
}

async function loadHistory() {
  if (!state.sessionId) return;
  try {
    const data = await api("/api/history?session_id=" + encodeURIComponent(state.sessionId));
    renderHistory(data.messages || []);
    maybeShowWelcome();
  } catch (_) { /* ignore */ }
}

/* ---------- 流式对话 + 停止生成 ---------- */

async function sendMessage(text) {
  if (state.sending) return;
  text = (text || "").trim() || (state.attachments.length ? "根据我上传的参考图生成商品图" : "");
  if (!text) return;
  if (!state.sessionId) {
    try { await initSession(); } catch (e) {
      toast("初始化失败: " + e.message, 4000);
      return;
    }
  }
  const welcome = els.chat.querySelector(".welcome");
  if (welcome) welcome.remove();
  const attachUrls = state.attachments.map((a) => a.url);
  addMessage("user", text, null, attachUrls);
  const bubble = addMessage("assistant", "");
  bubble.classList.add("typing");
  state.sending = true;
  state.abortCtrl = new AbortController();
  els.send.classList.add("stop");
  els.send.innerHTML = `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>`;
  els.send.title = "停止生成";
  els.input.disabled = true;
  els.input.value = "";
  clearAttachments();

  let stopped = false;
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        user_id: state.userId,
        session_id: state.sessionId,
        images: attachUrls,
      }),
      signal: state.abortCtrl.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let gotToken = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        for (const line of part.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (payload === "[DONE]") continue;
          let evt;
          try { evt = JSON.parse(payload); } catch (_) { continue; }
          handleEvent(evt);
          if (evt.type === "token" && evt.content) gotToken = true;
        }
      }
    }
    if (!gotToken && !bubble.textContent.trim()) {
      const emptyBody = bubble.querySelector(".md-body") || bubble;
      emptyBody.textContent = "（无回复）";
    }
  } catch (e) {
    if (e.name === "AbortError") {
      stopped = true;
    } else {
      bubble.classList.remove("typing");
      const errBody = bubble.querySelector(".md-body") || bubble;
      errBody.textContent = "⚠️ " + e.message;
      if (!state.health || !state.health.llm || !state.health.llm.configured) {
        toast("请到设置页填写 LLM API Key", 4000);
      }
    }
  } finally {
    state.sending = false;
    state.abortCtrl = null;
    els.send.classList.remove("stop");
    els.send.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>`;
    els.send.title = "发送";
    els.input.disabled = false;
    bubble.classList.remove("typing");
    const finalBody = bubble.querySelector(".md-body") || bubble;
    if (stopped) finalBody.textContent += "\n\n（已停止）";
    renderMessageBody(bubble, finalBody.textContent);
    els.input.focus();
    refreshMemory();
    refreshStudy();
    loadSessions();
  }
}

function stopGenerating() {
  if (state.abortCtrl) state.abortCtrl.abort();
}

function handleEvent(evt) {
  switch (evt.type) {
    case "token": {
      const bubble = currentAssistant();
      if (bubble) {
        const body = bubble.querySelector(".md-body") || bubble;
        body.textContent += evt.content || "";
        scrollBottom();
      }
      break;
    }
    case "cognition": {
      const n = evt.total || 0;
      const recs = evt.records || [];
      const text = recs.length ? recs.join("；") : n + " 条";
      addChip("🧠 已记录 " + n + " 条：" + text.slice(0, 120));
      break;
    }
    case "tool_call":
      addChip("🛠 " + (evt.name || "") + "(" + argText(evt.args) + ")");
      break;
    case "tool_result": {
      const tc = evt.tool_call || {};
      const raw = tc.data || tc.error || "";
      const text = (typeof raw === "object" && raw !== null)
        ? String(raw.content || JSON.stringify(raw) || "")
        : String(raw);
      addChip("✅ " + text.slice(0, 80));
      if (tc.data && tc.data.image_url) appendGeneratedImage(tc.data.image_url);
      break;
    }
    case "reflect":
      addChip("🔄 反思：" + (evt.reason || ""));
      break;
    case "context":
      addChip("📐 " + (evt.note || ""));
      break;
    case "budget_exhausted":
      addChip("⚠️ " + (evt.note || "预算耗尽"));
      break;
    case "error": {
      toast("出错：" + (evt.error || "未知错误"), 6000);
      const bubble = currentAssistant();
      if (bubble && !bubble.textContent.trim()) {
        bubble.textContent = "⚠️ " + (evt.error || "未知错误");
      }
      break;
    }
  }
}

function argText(args) {
  if (!args) return "";
  if (typeof args === "string") return args.slice(0, 60);
  return Object.entries(args).map(([k, v]) => k + "=" + String(v).slice(0, 30)).join(", ").slice(0, 120);
}

/* ---------- 学习页 ---------- */

const STUDY_PACE = { ahead: "超前", on_track: "按计划", behind: "落后", unknown: "" };
const STUDY_STATUS = { active: "进行中", paused: "已暂停", completed: "已完成", archived: "已归档" };

async function refreshStudy() {
  try {
    const data = await api("/api/study/overview");
    renderStudy(data);
  } catch (_) { /* ignore */ }
}

function studyForm(kind, plans) {
  const opts = plans.map((p) => `<option value="${esc(p.plan_id)}">${esc(p.title)}</option>`).join("");
  if (kind === "session") {
    return `<div class="study-form"><h4>打卡学习</h4>
      <select id="sf-plan">${opts}</select>
      <input id="sf-subject" placeholder="科目（如 英语）">
      <input id="sf-minutes" type="number" placeholder="分钟" min="1">
      <input id="sf-note" placeholder="备注（可选）">
      <button class="btn-mini" data-act="submit-session">保存</button></div>`;
  }
  if (kind === "test") {
    return `<div class="study-form"><h4>记测验</h4>
      <select id="sf-plan">${opts}</select>
      <input id="sf-title" placeholder="测验名称（如 词汇测验一）">
      <input id="sf-total" type="number" placeholder="总题数" min="1">
      <input id="sf-correct" type="number" placeholder="答对数" min="0">
      <input id="sf-kp" placeholder="知识点，逗号分隔（可选）">
      <button class="btn-mini" data-act="submit-test">保存</button></div>`;
  }
  return `<div class="study-form"><h4>记错题</h4>
    <select id="sf-plan">${opts}</select>
    <input id="sf-content" placeholder="错题内容">
    <input id="sf-reason" placeholder="归因（概念不清/粗心/没见过）">
    <input id="sf-kp" placeholder="关联知识点（可选）">
    <button class="btn-mini" data-act="submit-mistake">保存</button></div>`;
}

function renderStudy(data) {
  const plans = data.active_plans || [];
  els.studySub.textContent = (data.profile_summary || "").slice(0, 40);
  if (!plans.length) {
    els.studyBody.innerHTML = `<div class="study-empty">
      <div class="study-empty-icon">📚</div>
      <h3>还没有学习计划</h3>
      <p>去对话里说「帮我制定一个三个月过英语四级的计划」，生成学习计划后，这里会展示进度、复习与提醒。</p>
      <button class="btn-mini" data-go-chat="1">去对话制定计划 →</button>
    </div>`;
    const go = els.studyBody.querySelector("[data-go-chat]");
    if (go) go.addEventListener("click", () => switchTab("chat"));
    return;
  }
  const dueTotal = data.review_due_total || 0;
  const dueRemind = (data.due_reminders || []).length;
  const today = data.today_minutes || 0;
  let html = "";

  html += `<div class="study-stats">
    <div class="stat"><b>${today}</b><span>今日分钟</span></div>
    <div class="stat ${dueTotal ? "warn" : ""}"><b>${dueTotal}</b><span>到期复习</span></div>
    <div class="stat ${dueRemind ? "warn" : ""}"><b>${dueRemind}</b><span>到期提醒</span></div>
  </div>`;

  html += `<div class="study-actions">
    <button class="btn-mini" data-act="form-session">打卡学习</button>
    <button class="btn-mini" data-act="form-test">记测验</button>
    <button class="btn-mini" data-act="form-mistake">记错题</button>
  </div><div id="study-form-slot"></div>`;

  html += `<div class="group"><h3>学习计划</h3>`;
  for (const p of plans) {
    const info = p.progress_info || {};
    const pace = STUDY_PACE[info.pace] || "";
    const due = p.review_due || 0;
    const stagnant = p.stagnant_days != null && p.stagnant_days >= 3;
    html += `<div class="plan-card" data-pid="${esc(p.plan_id)}">
      <div class="plan-head"><b>${esc(p.title)}</b><span class="badge st-${esc(p.status)}">${STUDY_STATUS[p.status] || p.status}</span></div>
      <div class="bar"><i style="width:${Math.min(info.progress || 0, 100)}%"></i></div>
      <div class="plan-meta">
        <span>${info.progress || 0}%</span>
        ${pace ? `<span>${pace}</span>` : ""}
        <span>已学 ${info.total_minutes || 0} 分钟</span>
        ${info.milestones_total ? `<span>里程碑 ${info.milestones_done || 0}/${info.milestones_total}</span>` : ""}
      </div>
      <div class="plan-alerts">
        ${due ? `<span class="alert danger">复习到期 ${due} 项</span>` : ""}
        ${stagnant ? `<span class="alert warn">停滞 ${p.stagnant_days} 天</span>` : ""}
        ${p.status === "completed" ? `<span class="alert ok">已完成</span>` : ""}
      </div>
    </div>`;
  }
  html += "</div>";

  const due = data.due_reminders || [];
  const upcoming = data.upcoming_reminders || [];
  const reminders = [...due, ...upcoming.slice(0, 5)];
  html += `<div class="group"><h3>提醒</h3>`;
  if (!reminders.length) {
    html += `<div class="card muted">没有提醒。去对话里说「提醒我今晚8点复习英语」</div>`;
  }
  for (const r of reminders) {
    html += `<div class="card reminder">${r.done ? "✓ " : due.includes(r) ? "⏰ " : ""}${esc(r.title)}
      <button class="btn-mini" data-rid="${esc(r.reminder_id)}">完成</button>
      <div class="meta">${esc(String(r.remind_at || "").replace("T", " ").slice(0, 16))}</div>
    </div>`;
  }
  html += "</div>";

  els.studyBody.innerHTML = html;

  els.studyBody.querySelectorAll(".plan-card").forEach((card) => {
    card.addEventListener("click", () => loadPlanDetail(card.dataset.pid));
  });
  els.studyBody.querySelectorAll("[data-rid]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api("/api/study/complete_reminder", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reminder_id: btn.dataset.rid }),
        });
        toast("已标记完成");
        refreshStudy();
      } catch (e) {
        toast("操作失败: " + e.message);
      }
    });
  });
  els.studyBody.querySelectorAll("[data-act]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const slot = document.getElementById("study-form-slot");
      slot.innerHTML = studyForm(btn.dataset.act.slice(5), plans);
      bindStudyForm();
    });
  });
}

async function bindStudyForm() {
  const slot = document.getElementById("study-form-slot");
  const btn = slot && slot.querySelector("[data-act^=submit]");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const planId = slot.querySelector("#sf-plan").value;
    if (btn.dataset.act === "submit-session") {
      const subject = slot.querySelector("#sf-subject").value.trim();
      const minutes = parseInt(slot.querySelector("#sf-minutes").value, 10);
      if (!subject || !minutes) { toast("请填写科目与分钟"); return; }
      await api("/api/study/log_session", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject, minutes, plan_id: planId }),
      });
      toast("已记录学习");
    } else if (btn.dataset.act === "submit-test") {
      const title = slot.querySelector("#sf-title").value.trim();
      const total = parseInt(slot.querySelector("#sf-total").value, 10);
      const correct = parseInt(slot.querySelector("#sf-correct").value, 10);
      const kp = slot.querySelector("#sf-kp").value.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
      if (!title || !total) { toast("请填写测验名称与总题数"); return; }
      await api("/api/study/tests", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: planId, title, total, correct, knowledge_points: kp }),
      });
      toast("已记录测验");
    } else {
      const content = slot.querySelector("#sf-content").value.trim();
      const reason = slot.querySelector("#sf-reason").value.trim();
      const kp = slot.querySelector("#sf-kp").value.trim();
      if (!content) { toast("请填写错题内容"); return; }
      await api("/api/study/mistakes", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: planId, content, reason, knowledge_point: kp }),
      });
      toast("已记录错题");
    }
    slot.innerHTML = "";
    refreshStudy();
  });
}

async function loadPlanDetail(planId) {
  try {
    const d = await api("/api/study/plans/" + encodeURIComponent(planId));
    renderPlanDetail(d);
  } catch (e) {
    toast("加载失败: " + e.message);
  }
}

function renderPlanDetail(d) {
  const info = d.progress_info || {};
  let html = `<button class="btn-mini" data-back="1">← 返回</button>`;
  html += `<div class="plan-detail-head">
    <h2>${esc(d.title)}</h2>
    <span class="badge st-${esc(d.status)}">${STUDY_STATUS[d.status] || d.status}</span>
    <div class="bar"><i style="width:${Math.min(info.progress || 0, 100)}%"></i></div>
    <div class="plan-meta">
      <span>${info.progress || 0}%</span>
      ${STUDY_PACE[info.pace] ? `<span>${STUDY_PACE[info.pace]}</span>` : ""}
      <span>已学 ${info.total_minutes || 0} 分钟</span>
      ${d.end_date ? `<span>截止 ${esc(String(d.end_date).slice(0, 10))}</span>` : ""}
      ${d.daily_minutes ? `<span>每日 ${d.daily_minutes} 分钟</span>` : ""}
    </div>
  </div>`;

  const acc = d.acceptance || [];
  if (acc.length) {
    html += `<div class="group"><h3>验收标准</h3>`;
    for (const a of acc) {
      html += `<div class="card ${a.done ? "done" : ""}">${a.done ? "✅" : "⬜"} ${esc(a.title)}
        <div class="meta">${a.done ? "已通过" : "待达成"} · 验收靠测验数据</div></div>`;
    }
    html += "</div>";
  }

  const ms = d.milestones || [];
  if (ms.length) {
    const doneMs = ms.filter((m) => m.done).length;
    html += `<div class="group"><h3>里程碑 ${doneMs}/${ms.length}</h3><div class="bar"><i style="width:${Math.round(doneMs / ms.length * 100)}%"></i></div>`;
    for (const m of ms) {
      html += `<div class="card ${m.done ? "done" : ""}">${m.done ? "✅" : "⬜"} ${esc(m.title)}
        <div class="meta">${m.due_date ? "截止 " + esc(String(m.due_date).slice(0, 10)) : ""}${m.done_at ? " · " + esc(String(m.done_at).slice(0, 10)) : ""}</div></div>`;
    }
    html += "</div>";
  }

  const tests = d.tests || [];
  if (tests.length) {
    html += `<div class="group"><h3>测验记录</h3>`;
    for (const t of tests) {
      const color = t.accuracy >= 70 ? "ok" : t.accuracy >= 60 ? "warn" : "danger";
      html += `<div class="card">${esc(t.title)} <span class="badge ${color}">${t.accuracy}%</span>
        <div class="bar thin"><i class="${color}" style="width:${t.accuracy}%"></i></div>
        <div class="meta">${t.correct}/${t.total} 对 · ${esc(String(t.created_at || "").replace("T", " ").slice(0, 16))}${t.knowledge_points && t.knowledge_points.length ? " · " + esc(t.knowledge_points.join(", ")) : ""}</div></div>`;
    }
    html += "</div>";
  }

  const mistakes = d.mistakes || [];
  if (mistakes.length) {
    html += `<div class="group"><h3>错题 ${mistakes.filter((m) => !m.resolved).length} 未解决</h3>`;
    for (const m of mistakes) {
      html += `<div class="card ${m.resolved ? "done" : ""}">${m.resolved ? "✅" : "📕"} ${esc(m.content)}
        <div class="meta">${m.knowledge_point ? esc(m.knowledge_point) + " · " : ""}${m.reason ? "归因：" + esc(m.reason) : "未归因"}${m.resolved ? " · 已解决" : ""}</div></div>`;
    }
    html += "</div>";
  }

  const reviews = d.reviews || [];
  const dueReviews = reviews.filter((r) => !r.resolved);
  if (dueReviews.length) {
    html += `<div class="group"><h3>复习队列</h3>`;
    for (const r of dueReviews) {
      const dueNow = String(r.next_review_at || "") <= new Date().toISOString();
      html += `<div class="card">${dueNow ? "🔴" : "🟢"} ${esc(r.content)}
        <div class="meta">间隔 ${r.interval_days} 天 · 连对 ${r.streak} 次${dueNow ? " · 今天到期" : " · 下次 " + esc(String(r.next_review_at || "").slice(5, 16).replace("T", " "))}</div>
        <div class="review-btns">
          <button class="btn-mini ok" data-rev="${esc(r.review_item_id)}" data-ok="1">记住了</button>
          <button class="btn-mini danger" data-rev="${esc(r.review_item_id)}" data-ok="0">没记住</button>
        </div>
      </div>`;
    }
    html += "</div>";
  }

  const adj = d.adjustments || [];
  if (adj.length) {
    html += `<div class="group"><h3>方案调整记录</h3>`;
    for (const a of adj.slice(-3)) {
      const badge = a.conclusion === "effective" ? `<span class="badge ok">有效</span>` : a.conclusion === "ineffective" ? `<span class="badge danger">无效</span>` : a.conclusion === "pending_review" ? `<span class="badge warn">待裁决</span>` : `<span class="badge">观察中</span>`;
      html += `<div class="card">${esc(a.content)} ${badge}
        <div class="meta">${a.reason ? "原因：" + esc(a.reason) : ""}${a.window_days ? " · 观察 " + a.window_days + " 天" : ""}${a.evaluated_at ? " · 已结算 " + esc(String(a.evaluated_at).slice(0, 10)) : ""}</div></div>`;
    }
    html += "</div>";
  }

  if (d.status === "completed" && d.completion) {
    html += `<div class="group"><h3>完成报告</h3>
      <div class="card done">${esc(d.completion.report || "")}
        <div class="meta">完成于 ${esc(String(d.completion.completed_at || "").slice(0, 10))}</div></div></div>`;
  }

  html += `<div class="group"><h3>分析</h3>
    <div class="card"><button class="btn-mini" data-analysis="1">查看学习分析</button></div></div>`;

  els.studyBody.innerHTML = html;
  const back = els.studyBody.querySelector("[data-back]");
  if (back) back.addEventListener("click", () => refreshStudy());
  const ana = els.studyBody.querySelector("[data-analysis]");
  if (ana) ana.addEventListener("click", () => loadPlanAnalysis(d.plan_id));
  els.studyBody.querySelectorAll("[data-rev]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api("/api/study/plans/" + encodeURIComponent(d.plan_id) + "/reviews/" + encodeURIComponent(btn.dataset.rev) + "/checkin", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ correct: btn.dataset.ok === "1" }),
        });
        toast(btn.dataset.ok === "1" ? "记住了" : "已加入复习");
        loadPlanDetail(d.plan_id);
      } catch (e) {
        toast("操作失败: " + e.message);
      }
    });
  });
}

async function loadPlanAnalysis(planId) {
  try {
    const r = await api("/api/study/plans/" + encodeURIComponent(planId) + "/analysis");
    let html = `<button class="btn-mini" data-back="1">← 返回</button>`;
    html += `<div class="group"><h3>学习分析（近 ${r.days} 天）</h3>
      <div class="study-stats">
        <div class="stat"><b>${r.total_minutes || 0}</b><span>总分钟</span></div>
        <div class="stat ${r.stagnant ? "warn" : ""}"><b>${r.stagnant_days != null ? r.stagnant_days : "-"}</b><span>停滞天数</span></div>
        <div class="stat"><b>${r.avg_accuracy != null ? r.avg_accuracy + "%" : "-"}</b><span>平均正确率</span></div>
      </div>`;
    if (r.review_due != null) {
      html += `<div class="card">复习：到期 ${r.review_due} 项 / 共 ${r.review_total} 项，已掌握 ${r.review_resolved} 项${r.review_accuracy != null ? "，正确率 " + r.review_accuracy + "%" : ""}</div>`;
    }
    if (r.milestone_ratio != null) {
      html += `<div class="card">里程碑达成：${r.milestone_ratio}%</div>`;
    }
    html += `<div class="card">进度：${r.progress}%（节奏 ${STUDY_PACE[r.pace] || r.pace}）</div>`;
    if (r.adjustments_evaluated && r.adjustments_evaluated.length) {
      for (const adj of r.adjustments_evaluated) {
        html += `<div class="card">调整实验结算：${esc(adj.content.slice(0, 40))} → ${adj.conclusion === "effective" ? "有效" : adj.conclusion === "pending_review" ? "待裁决" : "无效"}</div>`;
      }
    }
    html += "</div>";
    els.studyBody.innerHTML = html;
    const back = els.studyBody.querySelector("[data-back]");
    if (back) back.addEventListener("click", () => loadPlanDetail(planId));
  } catch (e) {
    toast("分析加载失败: " + e.message);
  }
}

/* ---------- 设置页 ---------- */

async function renderSettings() {
  const llm = (state.health && state.health.llm) || {};
  let html = `<div class="set-group"><h3>连接与模型</h3>
    <div class="set-row"><span>状态</span><span class="${llm.configured ? "ok-text" : "warn-text"}">${llm.configured ? "已连接 · " + esc(llm.model || "") : "未配置"}</span></div>
    <div class="set-row"><span>接口</span><span class="dim">${esc(llm.base_url || "-")}</span></div>
    <div class="set-form">
      <input id="cfg-key" type="password" placeholder="API Key（已配置可留空）" autocomplete="off">
      <input id="cfg-url" placeholder="Base URL（默认 DeepSeek）">
      <input id="cfg-model" placeholder="模型（默认 deepseek-v4-flash）">
      <button class="btn-mini" id="btn-save-llm">保存并生效</button>
    </div>
    <div class="dim small">配置保存在本机 ~/.aion_agent/.env，重启不丢失；不会上传到任何平台。</div>
  </div>`;

  html += `<div class="set-group"><h3>数据与同步</h3>
    <div id="sync-body" class="set-inner"></div>
  </div>`;

  html += `<div class="set-group"><h3>记忆管理</h3>
    <div id="memory-body" class="set-inner"></div>
  </div>`;

  html += `<div class="set-group"><h3>开发者选项</h3>
    <details><summary>技能 / 工具（启停与权限）</summary><div id="skills-body" class="set-inner"></div></details>
  </div>`;

  html += `<div class="set-group"><h3>关于</h3>
    <div class="set-row"><span>版本</span><span class="dim">0.1.0</span></div>
    <div class="set-row"><span>数据</span><span class="dim">全部保存在本机，可随时导出带走</span></div>
    <div class="dim small">Aion Agent —— 有长期记忆的本地 AI 助手。无账号、无云端，数据主权归你。</div>
  </div>`;

  els.settingsBody.innerHTML = html;
  refreshSync();
  refreshMemory();
  refreshSkills();

  const saveBtn = document.getElementById("btn-save-llm");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const key = document.getElementById("cfg-key").value.trim();
      if (!key) { toast("请输入 API Key"); return; }
      const url = document.getElementById("cfg-url").value.trim();
      const model = document.getElementById("cfg-model").value.trim();
      try {
        const r = await api("/api/config/llm", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ api_key: key, base_url: url, model }),
        });
        state.health = { status: "ok", llm: r.llm };
        toast("已保存并生效");
        renderSettings();
      } catch (e) {
        toast("保存失败: " + e.message, 4000);
      }
    });
  }
}

function renderMemory(data) {
  const body = document.getElementById("memory-body");
  if (!body) return;
  const triples = data.triples || [];
  const notes = data.notes || [];
  let html = "";
  if (!triples.length && !notes.length) {
    html = `<div class="card muted">还没有记忆。对话后 agent 会自动沉淀对你的认知。</div>`;
  }
  for (const t of triples.slice(0, 50)) {
    html += `<div class="card mem-item">${esc(t.subject)}${esc(t.predicate)}${esc(t.object)}
      <button class="del" data-id="${esc(t.rel_id)}" title="删除">✕</button></div>`;
  }
  for (const n of notes.slice(0, 10)) {
    html += `<div class="card mem-item">📄 ${esc(n.title)}
      <button class="del" data-id="${esc(n.note_id)}" data-note="1" title="删除">✕</button></div>`;
  }
  body.innerHTML = html;
  body.querySelectorAll(".del").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (btn.dataset.note) {
        toast("笔记暂不支持单条删除", 2000);
        return;
      }
      try {
        await api("/api/memory/" + encodeURIComponent(btn.dataset.id), { method: "DELETE" });
        toast("已删除一条记忆");
        refreshMemory();
      } catch (e) {
        toast("删除失败: " + e.message);
      }
    });
  });
}

async function refreshMemory() {
  try {
    const data = await api("/api/memory?user_id=" + encodeURIComponent(state.userId));
    renderMemory(data);
  } catch (_) { /* ignore */ }
}


/* ---------- 数据与同步 ---------- */

async function refreshSync() {
  const body = document.getElementById("sync-body");
  if (!body) return;
  try {
    const st = await api("/api/sync/status");
    const files = Object.entries(st.files || {})
      .map((kv) => "<code>" + esc(kv[0]) + "</code> " + kv[1] + "B")
      .join(" ");
    body.innerHTML =
      '<div class="dim small">设备标识：<code>' + esc(st.device_id) + "</code></div>" +
      '<div class="dim small">参与同步的数据文件：' + files + "</div>" +
      '<div class="set-form">' +
        '<label>① 导出数据包</label>' +
        '<div class="dim small">把本机全部数据（记忆/会话/学习/任务/配置）打包成可迁移 JSON</div>' +
        '<button class="btn-mini" id="btn-sync-export">导出并复制</button>' +
        '<label>② 从另一台设备拉取</label>' +
        '<input id="sync-url" type="text" placeholder="http://192.168.x.x:8010">' +
        '<button class="btn-mini" id="btn-sync-pull">拉取并合并</button>' +
        '<label>③ 导入数据包</label>' +
        '<textarea id="sync-import-area" rows="3" placeholder="粘贴导出的 JSON 数据包"></textarea>' +
        '<button class="btn-mini" id="btn-sync-import">导入并合并</button>' +
        '<div class="dim small">合并规则：按记录 id 去重、时间新者优先；导入前建议先导出备份。</div>' +
      "</div>";
    document.getElementById("btn-sync-export").addEventListener("click", doSyncExport);
    document.getElementById("btn-sync-pull").addEventListener("click", doSyncPull);
    document.getElementById("btn-sync-import").addEventListener("click", doSyncImport);
  } catch (e) {
    body.innerHTML = '<div class="dim">同步状态加载失败：' + esc(e.message) + "</div>";
  }
}

async function doSyncExport() {
  try {
    const bundle = await api("/api/sync/export");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(JSON.stringify(bundle));
      toast("已导出并复制数据包到剪贴板", 2600);
    } else {
      const area = document.getElementById("sync-import-area");
      if (area) area.value = JSON.stringify(bundle);
      toast("已导出到下方输入框，请复制保存", 2600);
    }
  } catch (e) {
    toast("导出失败：" + e.message, 3000);
  }
}

async function doSyncPull() {
  const url = document.getElementById("sync-url").value.trim();
  if (!url) { toast("请输入对端地址", 2000); return; }
  try {
    const r = await api("/api/sync/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url }),
    });
    toast("已合并 " + (r.merged.total || 0) + " 个数据文件", 2600);
    refreshSync();
  } catch (e) {
    toast("拉取失败：" + e.message, 3200);
  }
}

async function doSyncImport() {
  const text = document.getElementById("sync-import-area").value.trim();
  if (!text) { toast("请先粘贴数据包 JSON", 2000); return; }
  try {
    const bundle = JSON.parse(text);
    const r = await api("/api/sync/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bundle: bundle }),
    });
    toast("已合并 " + (r.merged.total || 0) + " 个数据文件", 2600);
    refreshSync();
  } catch (e) {
    toast("导入失败：" + e.message, 3200);
  }
}

async function refreshSkills() {
  const body = document.getElementById("skills-body");
  if (!body) return;
  try {
    const data = await api("/api/skills");
    const toolsData = await api("/api/tools");
    const skills = data.skills || [];
    const tools = toolsData.tools || [];
    const policy = toolsData.policy || {};
    const blocked = new Set(policy.blocked || []);
    const confirm = new Set(policy.confirm || []);
    let html = "";
    const levelName = { system: "系统核心", builtin: "框架内置", skill: "技能" };
    for (const s of skills) {
      const fixed = s.level === "system" || s.level === "builtin";
      const badge = '<span class="badge">' + esc(levelName[s.level] || s.level) + "</span>" +
        (fixed ? '<span class="badge badge-fixed">🔒 固化</span>' : "");
      const btn = fixed
        ? '<span class="dim small">不可禁用</span>'
        : (s.enabled
            ? '<button class="btn-mini" data-skill="' + esc(s.name) + '" data-enable="0">停用</button>'
            : '<button class="btn-mini" data-skill="' + esc(s.name) + '" data-enable="1">启用</button>');
      html += '<div class="card">' +
        "<div><strong>" + esc(s.name) + "</strong> <span class=\"dim\">v" + esc(s.version) + "</span> " + badge + "</div>" +
        '<div class="dim small">' + esc(s.description) + "</div>" +
        '<div class="dim small">' + (s.tools || []).map(function (t) {
          const tag = blocked.has(t) ? " 🔒禁用" : (confirm.has(t) ? " ⚠️需确认" : "");
          return "<code>" + esc(t) + "</code>" + tag;
        }).join(" ") + "</div>" +
        "<div>" + btn + "</div>" +
        "</div>";
    }
    body.innerHTML = html || '<div class="dim">暂无技能</div>';
    body.querySelectorAll("[data-skill]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const name = btn.dataset.skill;
        const enabled = btn.dataset.enable === "1";
        try {
          await api("/api/skills/" + encodeURIComponent(name) + "/toggle", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: enabled }),
          });
          toast((enabled ? "已启用" : "已停用") + "技能：" + name, 2000);
          refreshSkills();
        } catch (e) {
          toast("操作失败: " + e.message);
        }
      });
    });
  } catch (e) {
    body.innerHTML = '<div class="dim">技能加载失败：' + esc(e.message) + "</div>";
  }
}

/* ---------- 提醒通知 ---------- */

const notifySeen = new Set();

function showNotify(item) {
  const title = item.title || "提醒";
  const body = item.content || String(item.remind_at || "").replace("T", " ").slice(5, 16);
  if (window.AionAndroid && window.AionAndroid.notify) {
    try { window.AionAndroid.notify(String(title), String(body)); } catch (_) { /* ignore */ }
  }
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification("⏰ " + title, { body: body }); } catch (_) { /* ignore */ }
  }
  els.notifyBanner.classList.remove("hidden");
  els.notifyBanner.innerHTML =
    '<span class="notify-title">⏰ ' + esc(title) + "</span>" +
    '<div class="notify-body">' + esc(body) + "</div>" +
    '<button class="icon-btn sm notify-close" title="知道了">✕</button>';
  els.notifyBanner.querySelector(".notify-close").addEventListener("click", () => {
    els.notifyBanner.classList.add("hidden");
  });
}

async function pollNotifications() {
  let data;
  try {
    data = await api("/api/study/notifications");
  } catch (_) { return; }
  const items = data.notifications || [];
  const fresh = items.filter((n) => n.reminder_id && !notifySeen.has(n.reminder_id));
  if (!fresh.length) return;
  for (const n of fresh) notifySeen.add(n.reminder_id);
  showNotify(fresh[fresh.length - 1]);
  try {
    await api("/api/study/notifications/ack", { method: "POST" });
  } catch (_) { /* ignore */ }
  refreshStudy();
}

function startNotificationPolling() {
  pollNotifications();
  setInterval(pollNotifications, 15000);
}

/* ---------- 配置横幅 ---------- */

function showConfigBanner(detail) {
  els.configBanner.innerHTML =
    "⚠️ 未配置 LLM：到「设置」页粘贴 DeepSeek API Key 即可开始使用（保存在本机，重启不丢失）。" +
    (detail ? '<div class="dim small">' + esc(detail) + "</div>" : "") +
    '<div class="banner-actions">' +
    '<button class="btn-mini" id="btn-open-settings">⚙️ 去设置</button>' +
    '<button class="icon-btn sm close" id="btn-hide-banner">✕</button></div>';
  els.configBanner.classList.remove("hidden");
  const openBtn = els.configBanner.querySelector("#btn-open-settings");
  if (openBtn) openBtn.addEventListener("click", () => switchTab("settings"));
  const closeBtn = els.configBanner.querySelector("#btn-hide-banner");
  if (closeBtn) closeBtn.addEventListener("click", () => els.configBanner.classList.add("hidden"));
}

/* ---------- 初始化 ---------- */

async function init() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  try {
    state.health = await api("/api/health");
    const llm = state.health.llm || {};
    els.model.textContent = llm.configured
      ? (llm.model || "已连接") + " · " + (llm.base_url || "")
      : "未连接";
    if (!llm.configured) showConfigBanner(llm.error || "");
  } catch (_) {
    els.model.textContent = "服务不可用";
  }
  try {
    await loadSessions();
    if (!state.sessionId && state.sessions.length) {
      state.sessionId = state.sessions[0].session_id;
      await loadSessions();
    }
    if (state.sessionId) {
      await loadHistory();
    } else {
      maybeShowWelcome();
    }
    refreshMemory();
    refreshStudy();
    startNotificationPolling();
  } catch (e) {
    toast("初始化失败: " + e.message, 4000);
  }
}

/* ---------- 事件绑定 ---------- */

els.tabbar.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

els.send.addEventListener("click", () => {
  if (state.sending) { stopGenerating(); return; }
  const text = els.input.value.trim();
  if (text || state.attachments.length) sendMessage(text);
});

els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const text = els.input.value.trim();
    if ((text || state.attachments.length) && !state.sending) sendMessage(text);
  }
});

function updateSendState() {
  els.send.disabled = (!els.input.value.trim() && !state.attachments.length) || state.sending;
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 140) + "px";
}

els.input.addEventListener("input", updateSendState);

els.attach.addEventListener("click", () => els.fileInput.click());

els.fileInput.addEventListener("change", async () => {
  const files = Array.from(els.fileInput.files || []);
  els.fileInput.value = "";
  for (const f of files) {
    if (!f.type.startsWith("image/")) { toast("仅支持图片文件", 3000); continue; }
    if (f.size > 10 * 1024 * 1024) { toast("图片过大（上限 10MB）", 3000); continue; }
    const fd = new FormData();
    fd.append("file", f);
    try {
      const data = await api("/api/upload", { method: "POST", body: fd });
      state.attachments.push({ url: data.url, name: data.name || f.name });
      renderAttachments();
    } catch (e) {
      toast("上传失败: " + e.message, 4000);
    }
  }
});

document.getElementById("btn-sessions").addEventListener("click", () => {
  loadSessions().then(openSheet);
});
document.getElementById("btn-settings").addEventListener("click", () => switchTab("settings"));
document.getElementById("btn-new-session").addEventListener("click", newSession);
document.getElementById("btn-close-sheet").addEventListener("click", closeSheet);
els.sheetMask.addEventListener("click", closeSheet);

init();
