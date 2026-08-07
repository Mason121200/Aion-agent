/* Aion Agent Web UI —— 会话 + 认知记忆 */
"use strict";

const state = {
  sessionId: null,
  userId: "chat_user",
  sending: false,
  health: null,
};

const els = {
  chat: document.getElementById("chat"),
  input: document.getElementById("input"),
  send: document.getElementById("btn-send"),
  status: document.getElementById("status-dot"),
  model: document.getElementById("model-info"),
  btnNew: document.getElementById("btn-new"),
  btnDel: document.getElementById("btn-del"),
  sessionSelect: document.getElementById("session-select"),
  configBanner: document.getElementById("config-banner"),
  btnMemory: document.getElementById("btn-memory"),
  memoryPanel: document.getElementById("memory-panel"),
  memoryBody: document.getElementById("memory-body"),
  memoryMask: document.getElementById("memory-mask"),
  btnCloseMemory: document.getElementById("btn-close-memory"),
  toast: document.getElementById("toast"),
  btnStudy: document.getElementById("btn-study"),
  studyPanel: document.getElementById("study-panel"),
  studyBody: document.getElementById("study-body"),
  studyMask: document.getElementById("study-mask"),
  btnCloseStudy: document.getElementById("btn-close-study"),
  notifyBanner: document.getElementById("notify-banner"),
};

/* ---------- 通用 ---------- */

function toast(text, ms = 2600) {
  els.toast.textContent = text;
  els.toast.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => els.toast.classList.remove("show"), ms);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
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

/* ---------- 渲染 ---------- */

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.textContent = text;
  els.chat.appendChild(el);
  scrollBottom();
  return el;
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
  for (const m of messages) {
    if (m.role === "user" || m.role === "assistant") {
      addMessage(m.role, m.content);
    }
  }
  maybeShowWelcome();
}

function maybeShowWelcome() {
  if (els.chat.querySelector(".msg") || els.chat.querySelector(".welcome")) return;
  const card = document.createElement("div");
  card.className = "welcome";
  card.innerHTML =
    '<div class="welcome-logo">A</div>' +
    "<h2>我是 Aion Agent</h2>" +
    "<p>一个有长期记忆的 AI 助手。告诉我你的名字、喜好或目标，我会记住，并在以后的对话里自然想起。</p>" +
    '<div class="welcome-tips">试试：我叫小王 · 我喜欢看电影 · 你还记得我吗</div>';
  els.chat.appendChild(card);
}

function renderMemory(data) {
  const dimNames = { user: "用户画像", self: "助手自身", world: "客观知识", env: "环境" };
  let html = "";
  const triples = data.triples || [];
  const groups = {};
  for (const t of triples) {
    (groups[t.dimension] = groups[t.dimension] || []).push(t);
  }
  for (const dim of ["user", "self", "world", "env"]) {
    const items = groups[dim] || [];
    if (!items.length) continue;
    html += `<div class="mem-group"><h3>${dimNames[dim] || dim}</h3>`;
    for (const t of items) {
      const conf = Math.round((t.confidence || 0) * 100);
      const confirmMark = t.is_confirmed ? " ✓" : "";
      html += `<div class="mem-item">
        ${t.subject}${t.predicate}${t.object}
        <button class="del" data-id="${esc(t.rel_id)}" title="删除">✕</button>
        <div class="meta">置信度 ${conf}% · 使用 ${t.usage_count || 0} 次${confirmMark}</div>
      </div>`;
    }
    html += "</div>";
  }
  const states = data.states || [];
  if (states.length) {
    html += `<div class="mem-group"><h3>活跃状态</h3>`;
    for (const st of states) {
      html += `<div class="mem-item">[${esc(st.state_type)}] ${esc(st.state_name)}
        <div class="meta">${esc(st.description || "")}${st.expires_at ? " · 过期: " + esc(st.expires_at.slice(0, 10)) : ""}</div>
      </div>`;
    }
    html += "</div>";
  }
  const notes = data.notes || [];
  if (notes.length) {
    html += `<div class="mem-group"><h3>笔记</h3>`;
    for (const n of notes) {
      html += `<div class="mem-item">${esc(n.title)}
        <div class="meta">${esc((n.summary || n.content || "").slice(0, 60))}</div>
      </div>`;
    }
    html += "</div>";
  }
  if (!html) html = `<div class="mem-group"><h3>暂无记忆</h3><p style="color:var(--muted)">对话后自动沉淀的认知会显示在这里。</p></div>`;
  els.memoryBody.innerHTML = html;
  els.memoryBody.querySelectorAll(".del").forEach((btn) => {
    btn.addEventListener("click", async () => {
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
  } catch (_) { /* 服务未就绪时静默 */ }
}

/* ---------- 学习面板 ---------- */

const STUDY_PACE = { ahead: "超前", on_track: "按计划", behind: "落后", unknown: "" };

function renderStudy(data) {
  let html = "";
  const plans = data.active_plans || [];
  if (plans.length) {
    html += `<div class="mem-group"><h3>进行中的计划</h3>`;
    for (const p of plans) {
      const info = p.progress_info || {};
      const pace = STUDY_PACE[info.pace] || "";
      html += `<div class="mem-item">${esc(p.title)}
        <span class="badge">${info.progress || 0}%</span>${pace ? ` <span class="badge">${pace}</span>` : ""}
        <div class="meta">${esc(p.subject || "")}${p.end_date ? " · 截止 " + esc(String(p.end_date).slice(0, 10)) : ""} · 已学 ${info.total_minutes || 0} 分钟</div>
      </div>`;
    }
    html += "</div>";
  } else {
    html += `<div class="mem-group"><h3>进行中的计划</h3><p style="color:var(--muted)">还没有计划。对我说：「帮我制定一个三个月备考英语的计划」</p></div>`;
  }

  const due = data.due_reminders || [];
  const upcoming = data.upcoming_reminders || [];
  const reminders = [...due, ...upcoming.slice(0, 5)];
  if (reminders.length) {
    html += `<div class="mem-group"><h3>提醒</h3>`;
    for (const r of reminders) {
      const icon = r.done ? "✓ " : due.includes(r) ? "⏰ " : "";
      html += `<div class="mem-item">${icon}${esc(r.title)}
        <button class="btn-mini" data-rid="${esc(r.reminder_id)}">完成</button>
        <div class="meta">${esc(String(r.remind_at || "").replace("T", " ").slice(0, 16))}${r.content ? " · " + esc(String(r.content).slice(0, 40)) : ""}</div>
      </div>`;
    }
    html += "</div>";
  } else {
    html += `<div class="mem-group"><h3>提醒</h3><p style="color:var(--muted)">没有提醒。对我说：「提醒我今晚8点复习英语」</p></div>`;
  }

  const sessionsToday = data.sessions_today || [];
  html += `<div class="mem-group"><h3>今日学习</h3>
    <div class="mem-item">今日累计 ${data.today_minutes || 0} 分钟
      <div class="meta">${sessionsToday.length ? sessionsToday.slice(0, 3).map((s) => esc(s.subject) + " " + s.minutes + "分钟").join("、") : "今天还没记录，学完后告诉我一声"}</div>
    </div></div>`;

  const mats = data.recent_materials || [];
  if (mats.length) {
    html += `<div class="mem-group"><h3>最近资料</h3>`;
    for (const m of mats.slice(0, 5)) {
      html += `<div class="mem-item">${esc(m.title)}
        <div class="meta">${esc(m.subject || "")}${m.source ? " · " + esc(String(m.source).slice(0, 40)) : ""}</div>
      </div>`;
    }
    html += "</div>";
  }

  html += `<div class="mem-group" style="opacity:.7"><h3>试试这样说</h3>
    <p style="color:var(--muted)">「帮我制定英语学习计划」·「记录今天学了 30 分钟英语」·「提醒我明天上午9点背单词」·「我学习进度怎么样了？」</p></div>`;

  els.studyBody.innerHTML = html;
  els.studyBody.querySelectorAll(".btn-mini").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api("/api/study/complete_reminder", {
          method: "POST",
          body: JSON.stringify({ reminder_id: btn.dataset.rid }),
        });
        toast("提醒已完成");
        refreshStudy();
      } catch (e) {
        toast("操作失败: " + e.message);
      }
    });
  });
}

async function refreshStudy() {
  try {
    const data = await api("/api/study/overview");
    renderStudy(data);
  } catch (_) { /* 服务未就绪时静默 */ }
}

/* ---------- 提醒通知 ---------- */

const notifySeen = new Set();

function showNotify(item) {
  const title = item.title || "提醒";
  const body = item.content || String(item.remind_at || "").replace("T", " ").slice(5, 16);
  // Android 原生通知（App 内 WebView 桥）
  if (window.AionAndroid && window.AionAndroid.notify) {
    try { window.AionAndroid.notify(String(title), String(body)); } catch (_) { /* ignore */ }
  }
  // 桌面浏览器通知（需授权）
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification("⏰ " + title, { body: body }); } catch (_) { /* ignore */ }
  }
  // 应用内横幅
  els.notifyBanner.classList.remove("hidden");
  els.notifyBanner.innerHTML =
    '<span class="notify-title">⏰ ' + esc(title) + "</span>" +
    '<div class="notify-body">' + esc(body) + "</div>" +
    '<button class="notify-close" title="知道了">✕</button>';
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

/* ---------- 会话 ---------- */

async function initSession() {
  const data = await api("/api/session", {
    method: "POST",
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
  } catch (_) { /* ignore */ }
}

async function loadSessions() {
  let list = [];
  try {
    const data = await api("/api/sessions?user_id=" + encodeURIComponent(state.userId));
    list = data.sessions || [];
  } catch (_) {
    return [];
  }
  const cur = state.sessionId;
  els.sessionSelect.innerHTML = "";
  if (list.length) {
    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = "选择会话…";
    els.sessionSelect.appendChild(ph);
  }
  for (const s of list) {
    const opt = document.createElement("option");
    opt.value = s.session_id;
    opt.textContent = sessionLabel(s);
    els.sessionSelect.appendChild(opt);
  }
  if (cur && list.some((s) => s.session_id === cur)) {
    els.sessionSelect.value = cur;
  } else {
    els.sessionSelect.value = "";
  }
  return list;
}

function sessionLabel(s) {
  const date = s.created_at ? s.created_at.slice(5, 16).replace("T", " ") : "";
  const preview = (s.preview || "").replace(/\s+/g, " ");
  const tag = preview ? preview.slice(0, 16) : (s.message_count ? "会话 " + date : "空会话");
  return tag + " · " + s.message_count + "条";
}

async function newSession() {
  await initSession();
  els.chat.innerHTML = "";
  await loadSessions();
  await loadHistory();
  await refreshMemory();
}

async function selectSession(sid) {
  if (!sid || sid === state.sessionId) return;
  state.sessionId = sid;
  els.chat.innerHTML = "";
  await loadHistory();
  await refreshMemory();
}

async function deleteCurrentSession() {
  if (!state.sessionId) return;
  try {
    await api("/api/session/" + encodeURIComponent(state.sessionId), { method: "DELETE" });
  } catch (e) {
    toast("删除失败: " + e.message);
    return;
  }
  toast("已删除会话");
  state.sessionId = null;
  els.chat.innerHTML = "";
  await loadSessions();
  maybeShowWelcome();
  await refreshMemory();
}

/* ---------- 流式对话 ---------- */

async function sendMessage(text) {
  if (state.sending) return;
  if (!state.sessionId) {
    try { await initSession(); } catch (e) {
      toast("初始化失败: " + e.message, 4000);
      return;
    }
  }
  const welcome = els.chat.querySelector(".welcome");
  if (welcome) welcome.remove();
  addMessage("user", text);
  const bubble = addMessage("assistant", "");
  bubble.classList.add("typing");
  state.sending = true;
  els.send.disabled = true;
  els.input.disabled = true;
  els.input.value = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        user_id: state.userId,
        session_id: state.sessionId,
      }),
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
      bubble.textContent = "（无回复）";
    }
  } catch (e) {
    bubble.classList.remove("typing");
    bubble.textContent = "⚠️ " + e.message;
    if (!state.health || !state.health.llm || !state.health.llm.configured) {
      toast("请先点击右上角「⚙️ 设置」填写 LLM API Key", 4000);
    }
  } finally {
    state.sending = false;
    els.send.disabled = false;
    els.input.disabled = false;
    bubble.classList.remove("typing");
    els.input.focus();
    refreshMemory();
    refreshStudy();
    loadSessions();
  }
}

function handleEvent(evt) {
  switch (evt.type) {
    case "token":
      const bubble = currentAssistant();
      if (bubble) {
        bubble.textContent += evt.content || "";
        scrollBottom();
      }
      break;
    case "cognition": {
      const n = evt.total || 0;
      const recs = evt.records || [];
      const text = recs.length ? recs.join("；") : `${n} 条`;
      addChip("🧠 已记录 " + n + " 条：" + text.slice(0, 120));
      break;
    }
    case "tool_call":
      addChip("🛠️ " + (evt.name || "") + "(" + argText(evt.args) + ")");
      break;
    case "tool_result":
      addChip("✅ " + String((evt.data || evt.error || "")).slice(0, 80));
      break;
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
      const msg = evt.error || "未知错误";
      toast("出错：" + msg, 6000);
      const bubble = currentAssistant();
      if (bubble && !bubble.textContent.trim()) {
        bubble.textContent = "⚠️ " + msg;
      }
      break;
    }
    case "final":
      break;
  }
}

function argText(args) {
  if (!args) return "";
  if (typeof args === "string") return args.slice(0, 60);
  return Object.entries(args).map(([k, v]) => k + "=" + String(v).slice(0, 30)).join(", ").slice(0, 120);
}

/* ---------- 初始化 ---------- */

async function init() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  try {
    state.health = await api("/api/health");
    const llm = state.health.llm || {};
    const configured = !!llm.configured;
    els.status.classList.toggle("on", configured);
    els.status.classList.toggle("off", !configured);
    els.model.textContent = configured
      ? llm.model + " · " + llm.base_url
      : "未配置 LLM";
    if (!configured) {
      showConfigBanner(llm.error || "");
    }
  } catch (_) {
    els.model.textContent = "服务不可用";
  }
  try {
    const list = await loadSessions();
    if (list.length) {
      state.sessionId = list[0].session_id;
      await loadHistory();
    } else {
      maybeShowWelcome();
    }
    await refreshMemory();
    await refreshStudy();
    startNotificationPolling();
  } catch (e) {
    toast("初始化失败: " + e.message, 4000);
  }
}

function showConfigBanner(detail) {
  els.configBanner.innerHTML =
    "⚠️ 未配置 LLM：请点击右上角「⚙️ 设置」按钮，粘贴 DeepSeek API Key 后点「保存」（保存在手机本地，重启不丢失）。" +
    (detail ? "<br><span style=\"opacity:.8\">" + esc(detail) + "</span>" : "") +
    "<div class=\"banner-actions\"><button id=\"btn-open-settings\">⚙️ 去设置</button>" +
    "<button class=\"close\" id=\"btn-hide-banner\">✕</button></div>";
  els.configBanner.classList.remove("hidden");
  const openBtn = els.configBanner.querySelector("#btn-open-settings");
  if (openBtn) openBtn.addEventListener("click", () => {
    if (window.AionAndroid && window.AionAndroid.openSettings) {
      window.AionAndroid.openSettings();
    } else {
      toast("请点击顶部「⚙️ 设置」按钮", 3000);
    }
  });
  const closeBtn = els.configBanner.querySelector(".close");
  if (closeBtn) closeBtn.addEventListener("click", () => els.configBanner.classList.add("hidden"));
}

/* ---------- 事件绑定 ---------- */

els.send.addEventListener("click", () => {
  const text = els.input.value.trim();
  if (text && !state.sending) sendMessage(text);
});

els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const text = els.input.value.trim();
    if (text && !state.sending) sendMessage(text);
  }
});

els.input.addEventListener("input", () => {
  els.send.disabled = !els.input.value.trim() || state.sending;
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 140) + "px";
});

els.btnNew.addEventListener("click", newSession);
els.btnDel.addEventListener("click", deleteCurrentSession);
els.sessionSelect.addEventListener("change", () => {
  const sid = els.sessionSelect.value;
  if (!sid || sid === state.sessionId) return;
  selectSession(sid);
});

function openMemory() {
  els.memoryPanel.classList.remove("hidden");
  els.memoryMask.classList.remove("hidden");
  refreshMemory();
}
function closeMemory() {
  els.memoryPanel.classList.add("hidden");
  els.memoryMask.classList.add("hidden");
}
els.btnMemory.addEventListener("click", openMemory);
els.btnCloseMemory.addEventListener("click", closeMemory);
els.memoryMask.addEventListener("click", closeMemory);

function openStudy() {
  els.studyPanel.classList.remove("hidden");
  els.studyMask.classList.remove("hidden");
  refreshStudy();
}
function closeStudy() {
  els.studyPanel.classList.add("hidden");
  els.studyMask.classList.add("hidden");
}
els.btnStudy.addEventListener("click", openStudy);
els.btnCloseStudy.addEventListener("click", closeStudy);
els.studyMask.addEventListener("click", closeStudy);

init();
