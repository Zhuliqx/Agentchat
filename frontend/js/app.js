/* Multi-Agent Platform 前端 UI 逻辑（ES Module）。
   核心状态/工具见 core.js，自定义滚动条见 scrollbar.js，入口见 main.js。 */
import {
  $, state, escapeHtml, renderMarkdown, parseError, api,
  AGENT_META, AGENT_NAME_RE,
  getToken, setAuth, getStoredUser, isLoggedIn, authHeaders,
} from "./core.js";
import { initCustomScrollbars } from "./scrollbar.js";
// ---------------- 初始化 ----------------
async function init() {
  initCustomScrollbars();
  bindEvents();
  refreshHealth();
  refreshAuthUI();
  appendWelcome(); // 首屏立即渲染欢迎卡片（已有会话时会被 switchSession 立即覆盖）
  await Promise.all([loadSessions(), loadDocuments(), loadMemories()]);
  initCustomScrollbars(); // 首屏渲染完成后兜底重建滚动条轨道（幂等，有轨道则跳过）
  if (state.sessions.length === 0) await newSession();
  else await switchSession(state.sessions[0].id);
}

async function refreshHealth() {
  try {
    const h = await api("/health");
    const ok = h.status === "ok";
    $("#health-dot").className = "dot " + (ok ? "ok" : "");
    $("#health-text").textContent = ok
      ? `服务正常 · MCP: ${h.mcp_servers.length} 个`
      : "部分组件异常";
  } catch (e) {
    $("#health-dot").className = "dot err";
    $("#health-text").textContent = "后端不可达";
  }
}

// ---------------- 认证（登录/注册/访客） ----------------

/** 更新登录按钮 / 用户徽标（初始化与登录/退出后调用）。 */
async function refreshAuthUI() {
  const loginBtn = $("#btn-login");
  const chip = $("#user-chip");
  closeUserMenu();
  // 已存 token 时尝试恢复用户信息；失效则清除
  if (getToken()) {
    try {
      const me = await api("/auth/me");
      setAuth(getToken(), me);
    } catch (_) {
      setAuth("", null);
    }
  }
  const user = getStoredUser();
  if (user) {
    if (loginBtn) loginBtn.hidden = true;
    if (chip) {
      chip.hidden = false;
      chip.textContent = `👤 ${user.username}`;
      chip.title = "账户菜单";
    }
    const menuName = $("#user-menu-name");
    if (menuName) menuName.textContent = user.username;
  } else {
    if (loginBtn) loginBtn.hidden = false;
    if (chip) {
      chip.hidden = true;
      chip.textContent = "";
    }
  }
}

// ---- 账户下拉菜单 ----
function closeUserMenu() {
  const menu = $("#user-menu");
  if (menu) menu.hidden = true;
}
function toggleUserMenu() {
  const menu = $("#user-menu");
  if (menu) menu.hidden = !menu.hidden;
}

/** 个人主页：在弹窗中展示当前账号信息。 */
async function openUserProfile() {
  const user = getStoredUser();
  if (!user) return;
  $("#modal-title").textContent = "🪪 个人主页";
  const body = $("#modal-body");
  body.innerHTML = `<div class="tt-loading">加载中…</div>`;
  $("#modal").hidden = false;
  try {
    const me = await api("/auth/me");
    const st = await api("/auth/stats");
    // 注意：模板开头不能有换行/缩进——modal-body 是 white-space:pre-wrap，
    // 前导文本节点会被渲染成空行，把横幅往下推
    body.innerHTML = `<div class="profile">
        <div class="profile-banner">
          <div class="profile-avatar">👤</div>
          <div class="profile-info">
            <div class="profile-name">${escapeHtml(st.username)} <span class="profile-badge">已登录</span></div>
            <div class="profile-id">用户 ID：${escapeHtml(me.id)}</div>
          </div>
        </div>
        <div class="profile-stats">
          <div class="profile-stat"><b>${st.session_count}</b><span>会话</span></div>
          <div class="profile-stat"><b>${st.message_count}</b><span>消息</span></div>
          <div class="profile-stat"><b>${st.memory_count}</b><span>记忆</span></div>
          <div class="profile-stat"><b>${st.document_count}</b><span>文档</span></div>
        </div>
        <div class="profile-details">
          <div class="profile-row"><span class="pi">🕐</span><span>注册时间</span><span class="pv">${new Date(st.created_at).toLocaleString()}</span></div>
          <div class="profile-row"><span class="pi">🗝</span><span>用户 ID</span><span class="pv mono">${escapeHtml(me.id)}</span></div>
          <div class="profile-row"><span class="pi">🧮</span><span>内容估算</span><span class="pv">约 ${st.token_estimate.toLocaleString()} tokens</span></div>
        </div>
      </div>`;
  } catch (e) {
    body.innerHTML = `<p class="tt-empty">加载失败：${escapeHtml(String(e.message || e))}</p>`;
  }
}

/** 切换账号：清除当前登录态，打开登录弹窗（可登录其他账号）。 */
function switchAccount() {
  closeUserMenu();
  setAuth("", null);
  refreshAuthUI();
  openAuthModal("login");
}

function openAuthModal(tab = "login") {
  setAuthTab(tab);
  $("#auth-err").hidden = true;
  $("#auth-modal").hidden = false;
  setTimeout(() => $("#auth-username").focus(), 0);
}

function setAuthTab(tab) {
  const isLogin = tab === "login";
  $("#tab-login").classList.toggle("active", isLogin);
  $("#tab-register").classList.toggle("active", !isLogin);
  $("#auth-submit").textContent = isLogin ? "登录" : "注册";
  $("#auth-username").autocomplete = isLogin ? "username" : "new-username";
  $("#auth-password").autocomplete = isLogin ? "current-password" : "new-password";
  $("#auth-username").value = "";
  $("#auth-password").value = "";
  $("#auth-err").hidden = true;
}

async function submitAuth(ev) {
  ev.preventDefault();
  const username = $("#auth-username").value.trim();
  const password = $("#auth-password").value;
  const isLogin = $("#tab-login").classList.contains("active");
  const errEl = $("#auth-err");
  try {
    const data = isLogin
      ? await api("/auth/login", {
          method: "POST",
          body: JSON.stringify({ username, password }),
        })
      : await api("/auth/register", {
          method: "POST",
          body: JSON.stringify({ username, password }),
        });
    if (isLogin) {
      setAuth(data.token, data.user);
      $("#auth-modal").hidden = true;
      await refreshAuthUI();
      await Promise.all([loadSessions(), loadMemories()]); // 用户会话/记忆隔离
      if (state.sessions.length === 0) await newSession();
      else await switchSession(state.sessions[0].id);
    } else {
      // 注册成功 → 自动切到登录
      setAuthTab("login");
      $("#auth-username").value = username;
      $("#auth-password").value = "";
      $("#auth-err").hidden = true;
      errEl.textContent = "注册成功，请登录";
      errEl.hidden = false;
      errEl.className = "auth-err ok";
    }
  } catch (e) {
    errEl.textContent = e.message;
    errEl.hidden = false;
    errEl.className = "auth-err";
  }
}

async function logoutUser() {
  setAuth("", null);
  await refreshAuthUI();
  await Promise.all([loadSessions(), loadMemories()]); // 回到访客数据域
  if (state.sessions.length === 0) await newSession();
  else await switchSession(state.sessions[0].id);
}

function guestContinue() {
  setAuth("", null);
  $("#auth-modal").hidden = true;
  refreshAuthUI();
}

// ---------------- 会话数据分析（B4） ----------------

async function openSessionStats() {
  const sid = state.currentSessionId;
  if (!sid) {
    alert("请先选择一个会话");
    return;
  }
  $("#modal-title").textContent = "📊 会话数据分析";
  const body = $("#modal-body");
  body.innerHTML = `<div class="tt-loading">加载中…</div>`;
  $("#modal").hidden = false;
  try {
    const s = await api(`/sessions/${sid}/stats`);
    const fmtDur = (sec) => {
      if (sec == null) return "—";
      if (sec < 60) return `${sec} 秒`;
      if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
      return `${Math.floor(sec / 3600)} 时 ${Math.floor((sec % 3600) / 60)} 分`;
    };
    const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
    body.innerHTML = `
      <div class="stats-grid">
        <div class="stat-card"><div class="stat-ic">💬</div><div class="stat-num">${s.message_count}</div><div class="stat-label">总消息数</div></div>
        <div class="stat-card"><div class="stat-ic">🔄</div><div class="stat-num">${s.rounds}</div><div class="stat-label">对话回合</div></div>
        <div class="stat-card"><div class="stat-ic">👥</div><div class="stat-num">${s.user_count} / ${s.assistant_count}</div><div class="stat-label">用户 / 助手</div></div>
        <div class="stat-card"><div class="stat-ic">🪙</div><div class="stat-num">${s.est_tokens.toLocaleString()}</div><div class="stat-label">约 Token 数</div></div>
      </div>
      <table class="stats-table">
        <tr><td>内容总量</td><td>${s.total_chars.toLocaleString()} 字符</td></tr>
        <tr><td>平均用户消息</td><td>${s.avg_user_chars} 字符</td></tr>
        <tr><td>平均助手回复</td><td>${s.avg_assistant_chars} 字符</td></tr>
        <tr><td>最长单次回复</td><td>${s.longest_response_chars} 字符</td></tr>
        <tr><td>首次消息</td><td>${fmtTime(s.first_at)}</td></tr>
        <tr><td>最后消息</td><td>${fmtTime(s.last_at)}</td></tr>
        <tr><td>对话时长</td><td>${fmtDur(s.duration_sec)}</td></tr>
      </table>`;
  } catch (e) {
    body.innerHTML = `<p class="tt-empty">加载失败：${escapeHtml(String(e.message || e))}</p>`;
  }
}

// ---------------- 定时/批处理任务（B5） ----------------

async function openTasksModal() {
  $("#modal-title").textContent = "⏱ 定时 / 批处理任务";
  const body = $("#modal-body");
  body.innerHTML = `<div class="tt-loading">加载中…</div>`;
  $("#modal").hidden = false;
  await renderTasks(body);
}

/** 渲染任务列表 + 新建表单。 */
async function renderTasks(body) {
  body.innerHTML = `
    <div class="tasks-toolbar">
      <button class="btn btn-primary btn-xs" id="btn-task-new">＋ 新建任务</button>
      <button class="btn btn-ghost btn-xs" id="btn-task-refresh">↻ 刷新</button>
    </div>
    <div class="tasks-list" id="tasks-list">加载中…</div>`;
  body.querySelector("#btn-task-refresh").onclick = () => renderTasks(body);
  body.querySelector("#btn-task-new").onclick = () => renderTaskForm(body);
  await loadTasksInto(body);
}

async function loadTasksInto(body) {
  const list = body.querySelector("#tasks-list");
  try {
    const tasks = await api("/tasks");
    if (!tasks.length) {
      list.innerHTML = `<div class="tt-empty">暂无任务。<button class="retry-btn" id="btn-task-new2">＋ 新建任务</button></div>`;
      body.querySelector("#btn-task-new2").onclick = () => renderTaskForm(body);
      return;
    }
    list.innerHTML = tasks
      .map(
        (t) => `
      <div class="task-item">
        <div class="task-head">
          <span class="task-name">${escapeHtml(t.name)}</span>
          <span class="task-badge">${escapeHtml(t.task_label)}</span>
          <span class="task-status ${t.last_status}">${t.enabled ? "● 启用" : "○ 停用"}</span>
        </div>
        <div class="task-desc">${escapeHtml(t.task_desc)}</div>
        <div class="task-meta">调度：<code>${escapeHtml(t.schedule)}</code>
          ${t.next_run_at ? `· 下次：${new Date(t.next_run_at).toLocaleString()}` : ""}
          ${t.last_run_at ? `· 上次：${new Date(t.last_run_at).toLocaleString()}` : ""}
          ${t.last_status ? `· 结果：<b>${t.last_status === "success" ? "成功" : t.last_status === "failed" ? "失败" : t.last_status}</b>` : ""}
        </div>
        ${t.last_error ? `<div class="task-error">${escapeHtml(t.last_error)}</div>` : ""}
        <div class="task-actions">
          <button class="btn btn-ghost btn-xs" data-task-run="${t.id}">▶ 立即执行</button>
          <button class="btn btn-ghost btn-xs" data-task-toggle="${t.id}" data-enabled="${t.enabled}">${t.enabled ? "⏸ 停用" : "▶ 启用"}</button>
          <button class="btn btn-ghost btn-xs" data-task-del="${t.id}">🗑 删除</button>
        </div>
      </div>`
      )
      .join("");
    list.querySelectorAll("[data-task-run]").forEach((b) => {
      b.onclick = async () => {
        b.textContent = "执行中…";
        b.disabled = true;
        try {
          await api(`/tasks/${b.dataset.taskRun}/run`, { method: "POST" });
        } catch (e) {
          alert("执行失败：" + e.message);
        }
        renderTasks(body);
      };
    });
    list.querySelectorAll("[data-task-toggle]").forEach((b) => {
      b.onclick = async () => {
        await api(`/tasks/${b.dataset.taskToggle}`, {
          method: "PATCH",
          body: JSON.stringify({ enabled: b.dataset.enabled !== "true" }),
        });
        renderTasks(body);
      };
    });
    list.querySelectorAll("[data-task-del]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm("确定删除该任务？")) return;
        await api(`/tasks/${b.dataset.taskDel}`, { method: "DELETE" });
        renderTasks(body);
      };
    });
  } catch (e) {
    list.innerHTML = `<div class="tt-empty">加载失败：${escapeHtml(String(e.message || e))}</div>`;
  }
}

/** 新建任务表单（类型下拉来自 /tasks/registry）。 */
async function renderTaskForm(body) {
  let registry = [];
  try {
    registry = await api("/tasks/registry");
  } catch (_) {}
  body.innerHTML = `
    <div class="task-form">
      <h4>＋ 新建定时任务</h4>
      <label>任务名称
        <input id="tf-name" placeholder="如：每夜重建索引" maxlength="100" />
      </label>
      <label>任务类型
        <select id="tf-type">
          ${registry.map((r) => `<option value="${r.type}">${escapeHtml(r.label)} — ${escapeHtml(r.desc)}</option>`).join("")}
        </select>
      </label>
      <label>调度表达式
        <input id="tf-schedule" placeholder="interval:3600 或 cron:*/30" value="interval:3600" />
        <small>interval:&lt;秒&gt; 固定间隔；cron:&lt;分钟&gt; 分钟级（如 */30、0）</small>
      </label>
      <div class="task-form-actions">
        <button class="btn btn-primary" id="tf-save">保存</button>
        <button class="btn btn-ghost" id="tf-cancel">取消</button>
      </div>
    </div>`;
  body.querySelector("#tf-cancel").onclick = () => renderTasks(body);
  body.querySelector("#tf-save").onclick = async () => {
    const name = body.querySelector("#tf-name").value.trim();
    const task_type = body.querySelector("#tf-type").value;
    const schedule = body.querySelector("#tf-schedule").value.trim();
    if (!name) return alert("请填写任务名称");
    try {
      await api("/tasks", {
        method: "POST",
        body: JSON.stringify({ name, task_type, schedule }),
      });
      renderTasks(body);
    } catch (e) {
      alert("创建失败：" + e.message);
    }
  };
}

// ---------------- 会话 ----------------
async function loadSessions() {
  state.sessions = await api("/sessions");
  renderSessions();
}

function renderSessions() {
  const list = $("#session-list");
  list.innerHTML = state.sessions
    .map((s) => {
      const active = !state.batchMode && s.id === state.currentSessionId;
      const selected = state.selectedSessions.has(s.id);
      const check = state.batchMode
        ? `<input type="checkbox" class="session-check" data-checkid="${s.id}" ${selected ? "checked" : ""} />`
        : "";
      const del = state.batchMode
        ? ""
        : `<button class="del" data-del="${s.id}" title="删除会话">🗑</button>`;
      return `
      <div class="session-item ${active ? "active" : ""} ${selected ? "selected" : ""}"
           data-id="${s.id}" data-toggle="${state.batchMode ? "select" : "open"}">
        ${check}
        <span class="title" title="双击重命名">${escapeHtml(s.title)}</span>
        ${del}
      </div>`;
    })
    .join("");
  updateBatchBar();
}

async function newSession() {
  const s = await api("/sessions", { method: "POST" });
  state.sessions.unshift(s);
  await switchSession(s.id);
}

async function switchSession(id) {
  state.currentSessionId = id;
  renderSessions();
  const s = state.sessions.find((x) => x.id === id);
  $("#chat-title").textContent = s ? s.title : "会话";
  $("#messages").innerHTML = "";
  const msgs = await api(`/sessions/${id}`);
  msgs.forEach((m) => {
    if (m.role === "user") appendMessage(m.content, "user");
    else if (m.role === "assistant") appendMessage(m.content, "assistant");
  });
  if (msgs.length === 0) appendWelcome();
  scrollBottom();
}

/** 当前打开的会话已删除时：切换到剩余第一个会话，否则回到欢迎页。 */
async function fallbackAfterCurrentSessionRemoved() {
  state.currentSessionId = null;
  if (state.sessions.length) await switchSession(state.sessions[0].id);
  else {
    $("#messages").innerHTML = "";
    appendWelcome();
  }
}

/** 会话重命名：双击标题 → 内嵌输入框 → 回车/失焦保存，Esc 取消。 */
function startRename(item, titleEl) {
  const id = item.dataset.id;
  const old = titleEl.textContent;
  const input = document.createElement("input");
  input.className = "rename-input";
  input.value = old;
  input.maxLength = 100;
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const restore = (text) => {
    const span = document.createElement("span");
    span.className = "title";
    span.title = "双击重命名";
    span.textContent = text;
    input.replaceWith(span);
  };
  const finish = async (save) => {
    if (done) return;
    done = true;
    const val = input.value.trim();
    if (save && val && val !== old) {
      try {
        const s = await api(`/sessions/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ title: val }),
        });
        const found = state.sessions.find((x) => x.id === id);
        if (found) found.title = s.title;
        renderSessions();
        if (state.currentSessionId === id) $("#chat-title").textContent = s.title;
      } catch (e) {
        restore(old);
        alert("重命名失败：" + e.message);
      }
    } else {
      restore(val || old);
    }
  };

  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") finish(true);
    else if (ev.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

async function deleteSession(id) {
  if (!confirm("确定删除该会话？")) return;
  await api(`/sessions/${id}`, { method: "DELETE" });
  state.sessions = state.sessions.filter((s) => s.id !== id);
  if (state.currentSessionId === id) await fallbackAfterCurrentSessionRemoved();
  renderSessions();
}

// ---------------- 批量删除会话 ----------------

/** 切换批量管理模式（勾选多个会话后一键删除）。 */
function toggleBatchMode() {
  state.batchMode = !state.batchMode;
  if (!state.batchMode) state.selectedSessions.clear();
  $("#batch-bar").hidden = !state.batchMode;
  $("#btn-batch-mode").classList.toggle("active", state.batchMode);
  renderSessions();
}

/** 更新批量操作栏（选中数量、删除按钮可用态）。 */
function updateBatchBar() {
  const bar = $("#batch-bar");
  if (!bar) return;
  const n = state.selectedSessions.size;
  $("#batch-count").textContent = n ? `(${n})` : "";
  $("#btn-batch-del").disabled = n === 0;
}

/** 全选 / 取消全选当前列表。 */
function toggleSelectAll() {
  const all = state.sessions.every((s) => state.selectedSessions.has(s.id));
  if (all) state.selectedSessions.clear();
  else state.sessions.forEach((s) => state.selectedSessions.add(s.id));
  renderSessions();
}

/** 批量删除选中的会话。 */
async function batchDeleteSessions() {
  const ids = [...state.selectedSessions];
  if (!ids.length) return;
  if (!confirm(`确定删除选中的 ${ids.length} 个会话？该操作不可恢复。`)) return;
  const r = await api("/sessions/batch-delete", {
    method: "POST",
    body: JSON.stringify({ ids }),
  });
  const removed = new Set(ids);
  state.sessions = state.sessions.filter((s) => !removed.has(s.id));
  state.selectedSessions.clear();
  // 若删除了当前打开的会话，切到剩余的第一个或回到欢迎页
  if (state.currentSessionId && removed.has(state.currentSessionId)) {
    await fallbackAfterCurrentSessionRemoved();
  }
  renderSessions();
  loadSessions(); // 刷新侧栏标题列表（标题可能已变化）
  return r;
}

// ---------------- 消息渲染 ----------------
function appendWelcome() {
  $("#messages").innerHTML = `
    <div class="welcome">
      <div class="welcome-orb">🤖</div>
      <h2 class="welcome-title">嗨，我是<span class="hl">多 Agent</span>平台助手</h2>
      <p class="welcome-sub">Supervisor 智能编排 · 自动路由到合适的 Agent 回答你</p>
      <div class="welcome-cards">
        <div class="w-card" data-q="知识库中有什么内容？">
          <div class="w-card-ic">📚</div>
          <div class="w-card-name">知识库问答</div>
          <div class="w-card-desc">向量 + BM25 混合检索，回答文档 / 产品问题</div>
          <div class="w-card-example">试试：知识库中有什么内容？</div>
        </div>
        <div class="w-card" data-q="帮我统计一下数据库里有多少个会话">
          <div class="w-card-ic">🗄</div>
          <div class="w-card-name">MCP 工具</div>
          <div class="w-card-desc">直连数据库与外部服务，执行真实查询与计算</div>
          <div class="w-card-example">试试：统计数据库会话数量</div>
        </div>
        <div class="w-card" data-q="搜索一下最近AI行业新闻">
          <div class="w-card-ic">🌐</div>
          <div class="w-card-name">联网搜索</div>
          <div class="w-card-desc">实时资讯一键检索，获取最新网络信息</div>
          <div class="w-card-example">试试：搜索最近 AI 新闻</div>
        </div>
      </div>
      <div class="welcome-hint">点击卡片即可快速提问 · 或直接输入你的问题</div>
    </div>`;
}

function appendMessage(content, role) {
  const div = document.createElement("div");
  div.className = "message " + role;
  const body = role === "assistant" ? renderMarkdown(content) : escapeHtml(content);
  div.innerHTML = `
    <div class="avatar">${role === "user" ? "🙂" : "🤖"}</div>
    <div class="bubble">${body}</div>`;
  $("#messages").appendChild(div);
  return div;
}

function appendEventFlow(container) {
  const flow = document.createElement("div");
  flow.className = "event-flow";
  // 编排轨道：签名元素——把 Agent 调度可视化为一串路由节点
  const orbit = document.createElement("div");
  orbit.className = "orbit";
  flow.appendChild(orbit);
  flow._orbit = orbit;
  container.querySelector(".bubble").appendChild(flow);
  return flow;
}

/** 编排轨道节点：在轨道上追加一个节点（start/tool/end/error）。 */
function appendOrbitNode(flow, type, label) {
  const orbit = flow._orbit;
  if (!orbit) return;
  const prevNodes = orbit.querySelectorAll(".orbit-node");
  // Supervisor 开始节点只在轨道为空时追加一次：
  // HITL 确认/resume 后后端会重新发送 start 事件，避免轨道出现重复的 Supervisor
  if (type === "start" && prevNodes.length) return;
  if (prevNodes.length) {
    const link = document.createElement("span");
    link.className = "orbit-link";
    orbit.appendChild(link);
  }
  const node = document.createElement("div");
  node.className = "orbit-node " + (type === "end" ? "done" : type);
  // 刚加入的节点脉冲发光，标识当前执行位置
  if (type === "tool") node.classList.add("pulse");
  const icon = type === "start" ? "◉" : type === "end" ? "✓" : type === "error" ? "!" : "◆";
  node.innerHTML = `<span class="orbit-ic">${icon}</span><span class="orbit-tx">${escapeHtml(label)}</span>`;
  orbit.appendChild(node);

  // 多节点放不下横向一行时：检测到折行 → 切换为纵向"时间线"布局（避免蛇形折行/完成孤立）
  const prev = prevNodes[prevNodes.length - 1];
  if (prev && node.offsetTop > prev.offsetTop) {
    orbit.classList.add("v");
  }

  // 前一个执行节点停止脉冲；流程结束（end/error）时清除所有脉冲，避免完成后仍在闪
  if (type === "tool") {
    prevNodes.forEach((n) => n.classList.remove("pulse"));
  } else if (type === "end" || type === "error") {
    orbit.querySelectorAll(".orbit-node").forEach((n) => n.classList.remove("pulse"));
  }
  scrollBottom();
}

/** 从事件文本提取 Agent 名并附用途（"工具: search_agent" → "search_agent · 联网"）。 */
function orbitLabel(type, text) {
  if (type === "start") return "Supervisor";
  if (type === "end") return "完成";
  if (type === "error") return "错误";
  const m = String(text || "").match(AGENT_NAME_RE);
  const name = m ? m[0] : null;
  if (name && AGENT_META[name]) return `${name} · ${AGENT_META[name][1]}`;
  return name || String(text || "").slice(0, 18);
}

function appendEventLine(flow, type, text) {
  // 编排轨道：只对实际执行事件（tool/start/end/error）渲染节点。
  // agent 事件是 tool 的重复总结（后端对同一次调用会发 agent + tool 两个事件），
  // 只响应 tool 可避免出现两个一模一样的节点。
  if (["start", "tool", "end", "error"].includes(type)) {
    appendOrbitNode(flow, type, orbitLabel(type, text));
  }
  // 文字日志只保留 error：启动/完成/工具调用已由轨道节点可视化，重复文字反而显得空
  if (type !== "error") return;
  const line = document.createElement("div");
  line.className = "event-line " + type;
  line.innerHTML = `<span class="tag">[错误]</span> ${escapeHtml(text)}`;
  flow.appendChild(line);
}

function scrollBottom() {
  const m = $("#messages");
  m.scrollTop = m.scrollHeight;
}

// ---------------- 发送消息（SSE 流式，支持失败重试） ----------------
async function sendMessage(options = {}) {
  if (state.sending) return;
  // 新对话前清除残留的 HITL 气泡引用（interrupt 挂起时 finally 不清，避免确认时丢失）
  state.hitlMsg = null;
  state.hitlFlow = null;
  const input = $("#input");
  const retry = options.retry || null;
  const fromModal = options.text != null; // 版本历史分叉：程序化发送，不清输入框
  const text = fromModal ? options.text : retry ? retry.text : input.value.trim();
  if (!text) return;

  if (!retry && !fromModal) input.value = "";
  autoResize();

  // 欢迎卡片：首次提问后移除，让位给对话流
  const welcome = document.querySelector(".welcome");
  if (welcome) welcome.remove();

  appendMessage(text, "user");

  // 创建 agent 事件气泡（实时显示 Agent 调度过程）
  const agentMsg = createThinkingBubble("思考中…");
  const flow = appendEventFlow(agentMsg);

  // 记录本次尝试，失败时提供"重试"按钮
  const payload = {
    session_id: state.currentSessionId,
    message: text,
    use_rag: retry ? retry.use_rag : $("#rag-toggle").checked,
    use_search: retry ? retry.use_search : $("#search-toggle").checked,
  };
  if (options.checkpoint_id) payload.checkpoint_id = options.checkpoint_id; // Time Travel 分叉起点
  state.lastAttempt = payload;
  await streamChat(payload, { agentMsg, flow });
}

/** 创建"思考中"Agent 气泡（sendMessage / resumeChat 共用）。 */
function createThinkingBubble(text) {
  const agentMsg = document.createElement("div");
  agentMsg.className = "message assistant";
  agentMsg.innerHTML = `
    <div class="avatar">🤖</div>
    <div class="bubble"><span class="thinking"><i></i><i></i><i></i></span> ${text}</div>`;
  $("#messages").appendChild(agentMsg);
  return agentMsg;
}

/** 统一发送 /chat/stream 请求并消费 SSE 流（sendMessage / resumeChat 共用）。 */
async function streamChat(payload, ctx) {
  const { agentMsg, flow } = ctx;
  state.sending = true;
  const controller = new AbortController();
  state.abortController = controller;
  setStopButton(true);
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!res.ok) throw await parseError(res);
    if (!res.body) throw new Error("当前浏览器不支持流式响应");

    // 逐帧解析 SSE（token / 事件 / message / HITL 确认卡片统一处理）
    const handleFrame = makeFrameHandlers({ agentMsg, flow });
    await readSSEStream(res, handleFrame);
  } catch (e) {
    if (e.name === "AbortError") {
      // 用户点击停止：正常中断，不提示错误
      agentMsg.querySelector(".thinking")?.remove();
      agentMsg.querySelector(".bubble").childNodes[0].textContent = "";
      appendMessage("⏹ 已停止生成。", "assistant");
    } else {
      agentMsg.querySelector(".thinking")?.remove();
      appendEventLine(flow, "error", String(e.message || e));
      appendMessage("抱歉，处理失败：" + e.message, "assistant");
      // SSE 断线/网络错误：提供重试按钮（复用本次消息）
      if (state.lastAttempt) appendRetryButton(flow, state.lastAttempt);
    }
  } finally {
    state.sending = false;
    state.abortController = null;
    // 注意：这里不能清 hitlMsg/hitlFlow——interrupt 挂起时确认卡片仍等待用户点击，
    // 清掉会导致确认后无法复用原气泡；引用由 sendMessage 开头 / resumeChat 取走时清理。
    setStopButton(false);
    refreshHealth();
    loadSessions();
    loadMemories(); // Agent 可能保存了新的长期记忆
    scrollBottom();
  }
}

/** 切换「发送 / 停止」按钮（生成中显示停止）。 */
function setStopButton(visible) {
  const stop = $("#btn-stop");
  const send = $("#btn-send");
  if (stop) stop.hidden = !visible;
  if (send) send.hidden = visible;
}

// ---------------- SSE 流解析（sendMessage / resumeChat 共用） ----------------

/** 读取 SSE 流，逐帧 JSON 解析后回调 handleFrame(ev)。 */
async function readSSEStream(res, handleFrame) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      await dispatchFrame(frame, handleFrame);
    }
  }
  if (buffer.trim()) await dispatchFrame(buffer, handleFrame);
}

async function dispatchFrame(frame, handleFrame) {
  if (!frame.startsWith("data:")) return;
  const raw = frame.slice(5).trim();
  if (!raw) return;
  let ev;
  try { ev = JSON.parse(raw); } catch (_) { return; }
  await handleFrame(ev);
}

/** 生成统一的帧处理器：token 增量、最终 message、HITL interrupt、error、普通事件。 */
function makeFrameHandlers(ctx) {
  const { agentMsg, flow } = ctx;
  let answerBubble = null;
  let answered = false;
  // ---- 流式 Markdown 渲染状态 ----
  let mdBuffer = ""; // 已收到的 token 文本累积
  let mdTimer = null;

  /** 流式渲染：未闭合代码块（奇数个 ```）时去掉最后一个围栏，避免末尾整段被吞进代码块。 */
  function mdForStream(text) {
    let count = 0, from = 0, idx = -1;
    while ((idx = text.indexOf("```", from)) !== -1) {
      count++;
      from = idx + 3;
    }
    if (count % 2 === 1) {
      const last = text.lastIndexOf("```");
      return text.slice(0, last) + text.slice(last + 3);
    }
    return text;
  }

  /** 节流渲染累积的 md（60ms 一批，避免每个 token 全量重渲染卡顿）。 */
  function scheduleMdRender() {
    if (mdTimer) return;
    mdTimer = setTimeout(() => {
      mdTimer = null;
      if (!answerBubble) return;
      answerBubble.querySelector(".bubble").innerHTML =
        renderMarkdown(mdForStream(mdBuffer));
      scrollBottom();
    }, 60);
  }

  /** 最终渲染：清掉定时器，按完整文本渲染一次（message 帧到达时）。 */
  function flushMdRender() {
    if (mdTimer) {
      clearTimeout(mdTimer);
      mdTimer = null;
    }
    if (answerBubble) {
      answerBubble.querySelector(".bubble").innerHTML = renderMarkdown(mdBuffer);
    }
  }

  return async function handleFrame(ev) {
    if (ev.type === "token") {
      // token 级流式：累积文本 → 节流实时渲染 Markdown（边回复边渲染 md）
      agentMsg.querySelector(".thinking")?.remove();
      agentMsg.querySelector(".bubble").childNodes[0].textContent = "";
      if (!answerBubble) answerBubble = appendMessage("", "assistant");
      mdBuffer += ev.content;
      scheduleMdRender();
    } else if (ev.type === "message") {
      // 会话 id（新建会话时才知道）
      if (ev.data && ev.data.session_id) {
        state.currentSessionId = ev.data.session_id;
        refreshSessionTitle(ev.data.session_id);
      }
      agentMsg.querySelector(".thinking")?.remove();
      agentMsg.querySelector(".bubble").childNodes[0].textContent = "";
      if (!answerBubble) answerBubble = appendMessage("", "assistant");
      mdBuffer = ev.content;
      flushMdRender(); // 完整答案：最终渲染
      answered = true;
      state.lastAttempt = null; // 成功收到答案，清除待重试记录
    } else if (ev.type === "interrupt") {
      // HITL：需要人工确认，渲染确认卡片
      if (mdTimer) { clearTimeout(mdTimer); mdTimer = null; }
      agentMsg.querySelector(".thinking")?.remove();
      agentMsg.querySelector(".bubble").childNodes[0].textContent = "";
      renderConfirmCard(flow, ev.content, ev.data || {});
    } else if (ev.type === "error") {
      if (mdTimer) { clearTimeout(mdTimer); mdTimer = null; }
      agentMsg.querySelector(".thinking")?.remove();
      appendEventLine(flow, "error", ev.content);
      if (!answered) appendMessage("抱歉，处理失败：" + ev.content, "assistant");
    } else {
      agentMsg.querySelector(".thinking")?.remove();
      agentMsg.querySelector(".bubble").childNodes[0].textContent = "";
      appendEventLine(flow, ev.type, ev.content);
    }
  };
}

/** HITL 确认卡片：显示待确认问题，提供确认/取消。 */
function renderConfirmCard(flow, question, data) {
  const card = document.createElement("div");
  card.className = "hitl-card";
  card.innerHTML = `
    <div class="hitl-question">⚠️ ${escapeHtml(question)}</div>
    <div class="hitl-actions">
      <button class="retry-btn hitl-confirm">✓ 确认执行</button>
      <button class="retry-btn hitl-cancel">✕ 取消</button>
    </div>`;
  const sid = data.session_id || state.currentSessionId || "";
  const resume = (choice) => {
    card.remove(); // 只移除确认卡片，保留气泡与轨道节点
    resumeChat(choice, sid);
  };
  card.querySelector(".hitl-confirm").onclick = () => resume("confirmed");
  card.querySelector(".hitl-cancel").onclick = () => resume("cancelled");
  flow.appendChild(card);
  // 卡片必须已挂载到 DOM，closest 才能找到所属气泡（记录引用供确认后复用同一气泡）
  state.hitlMsg = card.closest(".message") || null;
  state.hitlFlow = flow;
  scrollBottom();
}

/** HITL：从 interrupt 处继续（确认或取消），复用同一 session_id 与同一气泡/轨道。 */
async function resumeChat(choice, sessionId) {
  const pending = state.lastAttempt;
  if (!pending || state.sending) return;
  const sid = sessionId || pending.session_id || state.currentSessionId;

  // 取走确认卡片记录的气泡/轨道引用（用后即清），复用仍在 DOM 中的气泡继续轨道
  const hitlMsg = state.hitlMsg;
  const hitlFlow = state.hitlFlow;
  state.hitlMsg = null;
  state.hitlFlow = null;
  let agentMsg = null;
  let flow = null;
  if (hitlMsg?.isConnected && hitlFlow?.isConnected) {
    agentMsg = hitlMsg;
    flow = hitlFlow;
    agentMsg.querySelector(".thinking")?.remove();
    const b = agentMsg.querySelector(".bubble");
    if (b && b.childNodes[0] && b.childNodes[0].nodeType === Node.TEXT_NODE) {
      b.childNodes[0].textContent = "";
    }
  } else {
    agentMsg = createThinkingBubble("等待处理…");
    flow = appendEventFlow(agentMsg);
  }

  const payload = {
    session_id: sid,
    message: pending.message,
    use_rag: pending.use_rag,
    use_search: pending.use_search,
    resume: choice,
  };
  await streamChat(payload, { agentMsg, flow });
}

/** 在事件流里追加"重试"按钮（SSE 断线/失败后一键重发）。 */
function appendRetryButton(flow, attempt) {
  const btn = document.createElement("button");
  btn.className = "retry-btn";
  btn.textContent = "↻ 重试";
  btn.title = "重新发送这条消息";
  btn.onclick = () => {
    btn.remove();
    sendMessage({ retry: attempt });
  };
  flow.appendChild(btn);
  scrollBottom();
}

async function refreshSessionTitle(id) {
  // 从服务端刷新会话列表，更新标题
  await loadSessions();
  const s = state.sessions.find((x) => x.id === id);
  if (s) $("#chat-title").textContent = s.title;
}

// ---------------- 文档 ----------------
async function loadDocuments() {
  state.documents = await api("/rag/documents");
  renderDocuments();
}

function renderDocuments() {
  const list = $("#doc-list");
  $("#doc-count").textContent = state.documents.length ? `(${state.documents.length})` : "";
  if (state.documents.length === 0) {
    list.innerHTML = `<div style="color:var(--text-dim);font-size:12px;padding:4px">暂无文档</div>`;
    return;
  }
  list.innerHTML = state.documents
    .map(
      (d) => `
      <div class="doc-item">
        <span>${d.has_file ? "📄" : "🗂"}</span>
        <span class="name" data-src="${escapeHtml(d.source)}" title="点击查看/下载原始文件">${escapeHtml(d.filename)}</span>
        <span class="count">${d.chunks}块</span>
        <button class="del" data-del-src="${escapeHtml(d.source)}" title="删除">🗑</button>
      </div>`
    )
    .join("");
}

async function uploadFiles(files) {
  if (!files || files.length === 0) return;
  const form = new FormData();
  Array.from(files).forEach((f) => form.append("file", f));
  const res = await api("/rag/upload", { method: "POST", body: form });
  alert(`摄入完成：${res.filename} → ${res.chunks} 个分块`);
  await loadDocuments();
}

async function deleteDoc(source) {
  if (!confirm("确定从知识库删除该文档？")) return;
  await api(`/rag/documents?source=${encodeURIComponent(source)}`, { method: "DELETE" });
  await loadDocuments();
}

async function showDocContent(source) {
  $("#modal-title").textContent = "文档详情";
  const body = $("#modal-body");
  body.innerHTML = `
    <div style="font-size:12px;color:var(--text-dim)">来源路径：</div>
    <div style="word-break:break-all;margin-bottom:8px">${escapeHtml(source)}</div>
    <div>
      <button class="retry-btn" id="btn-download-file">⬇ 下载原始文件</button>
      <button class="retry-btn" id="btn-copy-src" style="margin-left:6px">📋 复制路径</button>
    </div>
    <div id="file-preview" style="margin-top:8px"></div>`;
  $("#modal").hidden = false;

  $("#btn-download-file").onclick = () => {
    window.open(`/api/rag/documents/file?source=${encodeURIComponent(source)}&download=1`, "_blank");
  };
  $("#btn-copy-src").onclick = () => {
    navigator.clipboard?.writeText(source).catch(() => {});
  };

  // 文本类文件内联预览（二进制仅提示下载）
  try {
    const res = await fetch(`/api/rag/documents/file?source=${encodeURIComponent(source)}`);
    if (!res.ok) return;
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("text")) {
      $("#file-preview").innerHTML =
        `<span style="color:var(--text-dim);font-size:12px">该文件为二进制格式，请下载查看。</span>`;
      return;
    }
    const text = await res.text();
    const preview = text.length > 3000 ? text.slice(0, 3000) : text;
    $("#file-preview").innerHTML =
      `<hr><pre class="file-preview">${escapeHtml(preview)}</pre>` +
      (text.length > 3000
        ? `<div style="color:var(--text-dim);font-size:12px">…（已截断，可下载完整文件）</div>`
        : "");
  } catch (_) {}
}

// ---------------- 长期记忆 ----------------
async function loadMemories(query = "") {
  const q = query.trim();
  state.memories = await api(
    q
      ? `/memory?user_id=default&query=${encodeURIComponent(q)}`
      : "/memory?user_id=default"
  );
  renderMemories();
}

function renderMemories() {
  const list = $("#memory-list");
  $("#memory-count").textContent = state.memories.length ? `(${state.memories.length})` : "";
  if (state.memories.length === 0) {
    list.innerHTML = `<div style="color:var(--text-dim);font-size:12px;padding:4px">暂无记忆</div>`;
    return;
  }
  list.innerHTML = state.memories
    .map(
      (m) => `
      <div class="memory-item" title="${escapeHtml(m.content)}">
        <span>🧠</span>
        <span class="text">${escapeHtml(m.content)}</span>
        <button class="del" data-mem-del="${m.id}" title="删除记忆">×</button>
      </div>`
    )
    .join("");
}

async function addMemory() {
  const input = $("#memory-input");
  const content = input.value.trim();
  if (!content) return;
  await api("/memory", {
    method: "POST",
    body: JSON.stringify({ user_id: "default", content }),
  });
  input.value = "";
  await loadMemories();
}

async function deleteMemory(id) {
  await api(`/memory/${id}`, { method: "DELETE" });
  await loadMemories();
}

// ---------------- 事件绑定 ----------------
function bindEvents() {
  $("#btn-new-session").onclick = newSession;

  // 侧边栏区块折叠（点击标题收起/展开，动画过渡，状态持久化）
  document.querySelectorAll(".section[data-collapsible]").forEach((sec) => {
    const key = "sec-fold-" + sec.dataset.collapsible;
    const title = sec.querySelector(".section-title");
    if (localStorage.getItem(key) === "1") sec.classList.add("collapsed");
    title.addEventListener("click", (e) => {
      if (e.target.closest("button")) return; // 批量/全选/删除等按钮不触发折叠
      animateToggleSection(sec);
    });
  });

  /** 折叠/展开：内容区高度平滑过渡到占满剩余空间，箭头同步旋转，下方视图平滑跟随。 */
  function animateToggleSection(sec) {
    const key = "sec-fold-" + sec.dataset.collapsible;
    const body = sec.querySelector(".section-body");
    const arrow = sec.querySelector(".sec-toggle");
    const title = sec.querySelector(".section-title");
    const collapsed = sec.classList.contains("collapsed");
    if (collapsed) {
      // 展开：区块从标题高度平滑增长到占满高度，箭头立即回正，操作按钮同步显示
      sec.classList.remove("collapsed");
      const actions = sec.querySelector(".session-title-actions");
      if (actions) actions.style.display = ""; // 立即恢复（折叠时被 inline 隐藏）
      sec.classList.add("animating"); // 锁定 flex:0，高度随动画增长
      sec.classList.remove("animating"); // 临时 flex:1 测量占满高度
      const fullH = sec.offsetHeight;
      sec.classList.add("animating");
      const target = Math.max(0, fullH - title.offsetHeight);
      arrow.style.transform = ""; // 同步：与内容一起回正
      body.style.height = "0px";
      body.style.opacity = "0";
      void body.offsetHeight; // 强制 reflow，确保过渡被识别
      requestAnimationFrame(() => {
        body.style.height = target + "px";
        body.style.opacity = "1";
      });
      setTimeout(() => {
        body.style.height = "";
        body.style.opacity = "";
        sec.classList.remove("animating"); // 恢复 flex:1 占满，高度已一致无跳变
        localStorage.setItem(key, "0");
      }, 320);
    } else {
      // 折叠：先锁定当前实际高度（在 flex:1 状态下测量，避免切 animating 后 flex-basis:auto 回弹到内容全高造成瞬间超高），再平滑收缩
      const curH = body.offsetHeight; // 当前实际高度（此时尚未切 animating，仍是 flex:1 占满状态）
      const actions = sec.querySelector(".session-title-actions");
      if (actions) actions.style.display = "none"; // 折叠瞬间同步隐藏，不等动画结束
      sec.classList.add("animating"); // 锁定 flex:0，inline height 覆盖 flex-basis
      body.style.height = curH + "px"; // 以当前高度为起点（不变，不超高）
      arrow.style.transform = "rotate(-90deg)"; // 同步：与内容一起收起
      void body.offsetHeight; // 强制 reflow，确保过渡被识别
      requestAnimationFrame(() => {
        body.style.height = "0px";
        body.style.opacity = "0";
      });
      setTimeout(() => {
        sec.classList.add("collapsed");
        sec.classList.remove("animating");
        if (actions) actions.style.display = ""; // 恢复，由 .collapsed 规则接管隐藏
        body.style.height = "";
        body.style.opacity = "";
        localStorage.setItem(key, "1");
      }, 320);
    }
  }

  // 批量管理
  $("#btn-batch-mode").onclick = toggleBatchMode;
  $("#btn-batch-all").onclick = toggleSelectAll;
  $("#btn-batch-del").onclick = batchDeleteSessions;

  $("#session-list").addEventListener("click", (e) => {
    // 批量模式：点击勾选/取消勾选
    if (state.batchMode) {
      const cb = e.target.closest(".session-check");
      if (cb) {
        if (cb.checked) state.selectedSessions.add(cb.dataset.checkid);
        else state.selectedSessions.delete(cb.dataset.checkid);
        cb.closest(".session-item").classList.toggle("selected", cb.checked);
        updateBatchBar();
        return;
      }
      const item = e.target.closest("[data-toggle='select']");
      if (item) {
        const id = item.dataset.id;
        const cb = item.querySelector(".session-check");
        if (state.selectedSessions.has(id)) {
          state.selectedSessions.delete(id);
          if (cb) cb.checked = false;
        } else {
          state.selectedSessions.add(id);
          if (cb) cb.checked = true;
        }
        item.classList.toggle("selected", state.selectedSessions.has(id));
        updateBatchBar();
      }
      return;
    }
    // 普通模式：删除 / 打开会话
    const del = e.target.closest("[data-del]");
    if (del) return deleteSession(del.dataset.del);
    const item = e.target.closest("[data-id]");
    if (item) switchSession(item.dataset.id);
  });

  // 会话重命名：双击标题内嵌编辑（替代原生 prompt，VS Code 浏览器不支持）
  $("#session-list").addEventListener("dblclick", (e) => {
    if (state.batchMode) return;
    const title = e.target.closest(".title");
    const item = title && title.closest("[data-id]");
    if (!item) return;
    e.preventDefault();
    startRename(item, title);
  });

  $("#btn-send").onclick = sendMessage;
  $("#btn-stop").onclick = () => state.abortController?.abort();
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  $("#input").addEventListener("input", autoResize);
  autoResize(); // 初始为空 → 发送按钮置灰

  $("#messages").addEventListener("click", (e) => {
    const sug = e.target.closest("[data-q]");
    if (sug) {
      $("#input").value = sug.dataset.q;
      sendMessage();
    }
  });

  // 上传
  const zone = $("#upload-zone");
  zone.onclick = () => $("#file-input").click();
  $("#file-input").addEventListener("change", (e) => uploadFiles(e.target.files));
  ["dragover", "dragenter"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("dragging"); })
  );
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("dragging"); })
  );
  zone.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));

  // 文档列表
  $("#doc-list").addEventListener("click", (e) => {
    const del = e.target.closest("[data-del-src]");
    if (del) return deleteDoc(del.dataset.delSrc);
    const name = e.target.closest("[data-src]");
    if (name) showDocContent(name.dataset.src);
  });

  // 长期记忆
  $("#btn-add-memory").onclick = addMemory;
  $("#memory-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addMemory();
  });
  // 记忆语义搜索（防抖 250ms；空则回到全量）
  const memSearch = $("#memory-search");
  if (memSearch) {
    let memTimer = null;
    memSearch.addEventListener("input", () => {
      clearTimeout(memTimer);
      memTimer = setTimeout(() => loadMemories(memSearch.value), 250);
    });
  }
  $("#memory-list").addEventListener("click", (e) => {
    const del = e.target.closest("[data-mem-del]");
    if (del) deleteMemory(del.dataset.memDel);
  });

  $("#modal-close").onclick = () => ($("#modal").hidden = true);
  $("#modal").addEventListener("click", (e) => {
    if (e.target === $("#modal")) $("#modal").hidden = true;
  });

  // 版本历史（Time Travel）：打开时间线 / 从某步分叉重跑
  // 注：不使用原生 prompt()/alert()——VS Code 集成浏览器会抛 "prompt() is not supported"
  $("#btn-timetravel").onclick = openTimeTravelModal;
  $("#btn-export").onclick = exportSession;
  $("#btn-stats").onclick = openSessionStats;
  $("#btn-tasks").onclick = openTasksModal;

  // 认证：登录按钮 / 弹窗开关 / 标签页 / 提交 / 访客 / 退出
  $("#btn-login").onclick = () => openAuthModal("login");
  $("#auth-close").onclick = () => ($("#auth-modal").hidden = true);
  $("#auth-modal").addEventListener("click", (e) => {
    if (e.target === $("#auth-modal")) $("#auth-modal").hidden = true;
  });
  $("#tab-login").onclick = () => setAuthTab("login");
  $("#tab-register").onclick = () => setAuthTab("register");
  $("#auth-form").addEventListener("submit", submitAuth);
  $("#btn-guest").onclick = guestContinue;
  const userChip = $("#user-chip");
  if (userChip) userChip.onclick = (e) => {
    e.stopPropagation(); // 阻止 document 点击关闭（避免刚打开就关闭）
    toggleUserMenu();
  };
  // 菜单项：个人主页 / 切换账号 / 退出登录
  const userMenu = $("#user-menu");
  if (userMenu) {
    userMenu.addEventListener("click", (e) => {
      const item = e.target.closest("[data-action]");
      if (!item) return;
      const action = item.dataset.action;
      if (action === "profile") {
        closeUserMenu();
        openUserProfile();
      } else if (action === "switch") {
        switchAccount();
      } else if (action === "logout") {
        closeUserMenu();
        logoutUser();
      }
    });
  }
  // 点击页面其他区域关闭菜单
  document.addEventListener("click", closeUserMenu);
  $("#modal-body").addEventListener("click", (e) => {
    const doBtn = e.target.closest(".tt-do");
    if (doBtn) {
      const item = doBtn.closest(".tt-item");
      const input = item.querySelector(".tt-input");
      const text = (input ? input.value : "").trim();
      if (!text) { if (input) input.focus(); return; }
      $("#modal").hidden = true;
      sendMessage({ text, checkpoint_id: doBtn.dataset.cid });
      return;
    }
    const cancelBtn = e.target.closest(".tt-cancel");
    if (cancelBtn) {
      const item = cancelBtn.closest(".tt-item");
      item.querySelector(".tt-fork-box")?.remove();
      const forkBtn = item.querySelector(".tt-fork");
      if (forkBtn) forkBtn.hidden = false;
      return;
    }
    const forkBtn = e.target.closest(".tt-fork");
    if (!forkBtn) return;
    // 在该条目内展开内嵌输入区（替代原生 prompt）
    const item = forkBtn.closest(".tt-item");
    if (item.querySelector(".tt-fork-box")) return;
    forkBtn.hidden = true;
    const box = document.createElement("div");
    box.className = "tt-fork-box";
    box.innerHTML = `
      <input class="tt-input" placeholder="输入要从该步重新生成的消息…" />
      <div class="tt-fork-actions">
        <button class="btn btn-ghost tt-do" data-cid="${forkBtn.dataset.cid}">确认重跑</button>
        <button class="btn btn-ghost tt-cancel">取消</button>
      </div>`;
    item.appendChild(box);
    box.querySelector(".tt-input").focus();
  });
}


// ---------------- Time Travel（版本历史 / 分叉重跑） ----------------

/** 导出当前会话为 Markdown 文件（前端 Blob 下载）。 */
async function exportSession() {
  const sid = state.currentSessionId;
  if (!sid) {
    alert("请先选择一个会话");
    return;
  }
  try {
    const r = await api(`/sessions/${sid}/export`);
    const blob = new Blob([r.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const title = state.sessions.find((s) => s.id === sid)?.title || "会话";
    a.download = `${title}.md`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("导出失败：" + e.message);
  }
}

/** 打开版本历史 modal：拉取会话 checkpoint 时间线，支持从某步分叉。 */
async function openTimeTravelModal() {
  const sid = state.currentSessionId;
  if (!sid) {
    alert("请先选择一个会话");
    return;
  }
  $("#modal-title").textContent = "⏪ 版本历史（Time Travel）";
  const body = $("#modal-body");
  body.innerHTML = `<div class="tt-loading">加载中…</div>`;
  $("#modal").hidden = false;
  try {
    const res = await fetch(`/api/sessions/${sid}/checkpoints`);
    if (!res.ok) throw await parseError(res);
    const ckpts = await res.json();
    if (!ckpts.length) {
      body.innerHTML = `<p class="tt-empty">该会话暂无版本历史（需先完成一轮对话）。</p>`;
      return;
    }
    body.innerHTML = `<div class="tt-list">${ckpts.map((c, i) => ttItem(c, i, ckpts.length)).join("")}</div>`;
  } catch (e) {
    body.innerHTML = `<p class="tt-empty">加载失败：${escapeHtml(String(e.message || e))}</p>`;
  }
}

/** Time Travel 节点友好名：agent 名复用 AGENT_META，内部节点单独映射。 */
function ttNodeLabel(name) {
  const meta = AGENT_META[name];
  if (meta) return `${meta[0]} ${meta[1]}`;
  return { model: "🧠 思考", tools: "🔧 工具", __start__: "🚀 开始" }[name] || name;
}

/** 渲染单条 checkpoint（#1 = 最新）。 */
function ttItem(c, i, total) {
  const idx = total - i; // 1 = 最新
  const time = c.created_at ? new Date(c.created_at).toLocaleTimeString() : "";
  const summary = (c.summary || "(无文本步骤)").slice(0, 72);
  const badge = c.interrupted
    ? `<span class="tt-badge">⏸ 待确认</span>`
    : c.next && c.next.length
      ? `<span class="tt-badge tt-next">下一步: ${escapeHtml(c.next.map((n) => ttNodeLabel(n)).join(", "))}</span>`
      : `<span class="tt-badge tt-done">✓ 完成</span>`;
  return `
    <div class="tt-item">
      <div class="tt-head">
        <span class="tt-idx">#${idx}</span>
        <span class="tt-time">${time}</span>
        ${badge}
      </div>
      <div class="tt-summary">${escapeHtml(summary)}</div>
      <button class="btn btn-ghost tt-fork" data-cid="${escapeHtml(c.checkpoint_id || "")}" title="从该步骤分叉重新生成，不影响原历史">🔄 从这步重跑</button>
    </div>`;
}

function autoResize() {
  const el = $("#input");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 150) + "px";
  // 输入为空 → 发送按钮置灰（主流聊天应用交互）
  const send = $("#btn-send");
  if (send) send.disabled = !el.value.trim();
}

// ---------------- 启动（由 main.js 调用） ----------------
export { init };
