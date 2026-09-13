import { defineStore } from "pinia";
import { normalizeSources } from "@/utils/sources";
import { reactive } from "vue";
import { sessionsApi, streamChat } from "@/api";
import { useSessionsStore } from "./sessions";
import { useChatOptionsStore } from "./chatOptions";
import { useDialogStore } from "./dialog";
import { AGENT_META } from "@/utils/agentMeta";
import type { Message, SourceRef, SSEEvent } from "@/types/api";

export type OrbitType = "start" | "agent" | "tool" | "end" | "error";
export interface OrbitNode {
  type: OrbitType;
  label: string;
  active?: boolean; // 工具调用执行中（轨道节点闪烁）
}

export interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string; // Markdown 原文（流式时逐步累积，组件节流渲染）
  streaming?: boolean;
  orbit?: OrbitNode[];
  hitl?: { question: string; sessionId: string } | null;
  backendId?: string; // 后端 messages.id（用于删除/定位）
  sources?: SourceRef[]; // 引用溯源：RAG 检索命中的文档来源（含命中片段数）
  createdAt?: string; // ISO 时间，hover 消息时显示
}

export interface SendOptions {
  useRag?: boolean;
  useSearch?: boolean;
  useMemory?: boolean;
  checkpointId?: string; // Time Travel 分叉起点
}

const ORBIT_NAME_RE = new RegExp(Object.keys(AGENT_META).join("|"));

let uid = 0;
const nid = () => `m${Date.now()}_${uid++}`;

/** 距底部小于该值视为"贴底"，继续自动跟随流式输出 */
const STICK_TOLERANCE = 40;
/** 距底部超过该值才显示"回到最新"，避免轻微滚动就弹出来 */
const JUMP_BUTTON_GAP = 240;

function orbitLabel(type: OrbitType, text: string): string {
  if (type === "start") return "Supervisor";
  if (type === "end") return "完成";
  if (type === "error") return "错误";
  const m = String(text || "").match(ORBIT_NAME_RE);
  const name = m ? m[0] : null;
  if (name && AGENT_META[name]) return `${name} · ${AGENT_META[name].label}`;
  return name || String(text || "").slice(0, 18);
}

export const useChatStore = defineStore("chat", {
  state: () => ({
    messages: [] as ChatMsg[],
    sending: false,
    abortController: null as AbortController | null,
    historySeq: 0,
    historyError: null as string | null,
    // HITL：interrupt 时记录所在消息 id，确认后在同一个气泡/轨道内继续
    hitlMsgId: null as string | null,
    // 正在发送的用户消息（SSE meta 帧回填其后端 id）
    pendingUserMsg: null as ChatMsg | null,
    /** 消息列表距底部的距离（由 MessageList 在滚动时写入） */
    scrollGap: 0,
    /** 离开底部那一刻的消息条数，用于统计"新消息" */
    unseenBase: 0,
    /** 自增计数：输入区点击"回到最新"时通知列表滚到底部 */
    scrollNonce: 0,
  }),
  getters: {
    lastAssistant: (s): ChatMsg | null => {
      for (let i = s.messages.length - 1; i >= 0; i--) {
        if (s.messages[i].role === "assistant") return s.messages[i];
      }
      return null;
    },
    atBottom: (s): boolean => s.scrollGap <= STICK_TOLERANCE,
    /** 离开底部后新增的消息条数（含用户刚发出的那条） */
    unseen: (s): number =>
      s.scrollGap <= STICK_TOLERANCE ? 0 : Math.max(0, s.messages.length - s.unseenBase),
    showJumpButton: (s): boolean =>
      s.scrollGap > JUMP_BUTTON_GAP ||
      (s.scrollGap > STICK_TOLERANCE && s.messages.length > s.unseenBase),
  },
  actions: {
    /** 由 MessageList 在滚动时调用，维护贴底状态与新消息基准 */
    markScroll(gap: number) {
      const atBottom = gap <= STICK_TOLERANCE;
      const wasAtBottom = this.scrollGap <= STICK_TOLERANCE;
      // 刚离开底部（或已贴底）时把基准对齐到当前条数，之后的增量就是"新消息"
      if (atBottom || wasAtBottom) this.unseenBase = this.messages.length;
      this.scrollGap = gap;
    },

    /** 输入区点击"回到最新"：复位状态并通知列表滚到底 */
    jumpToBottom() {
      this.scrollGap = 0;
      this.unseenBase = this.messages.length;
      this.scrollNonce += 1;
    },

    async loadHistory(sessionId: string) {
      this._abortStream();
      const seq = ++this.historySeq;
      this.messages = [];
      this.historyError = null;
      let msgs: Message[];
      try {
        msgs = await sessionsApi.history(sessionId);
      } catch (e) {
        if (seq !== this.historySeq) return;
        this.historyError = (e as Error).message || "加载会话失败";
        return;
      }
      if (seq !== this.historySeq) return; // 已被更晚的会话切换取代
      msgs.forEach((m: Message) => {
        this.messages.push({
          id: nid(),
          backendId: m.id,
          role: m.role === "user" ? "user" : "assistant",
          content: m.content,
          sources: normalizeSources(m.sources),
          createdAt: m.created_at,
        });
      });
    },
    clear() {
      this._abortStream();
      this.messages = [];
      this.historyError = null;
      this.hitlMsgId = null;
    },

    /** 切走会话/清空时中止在途流：立即释放 sending，
     *  旧流的 finally 通过 controller 身份判断，不能反向覆盖新流状态。 */
    _abortStream() {
      if (!this.abortController) return;
      this.abortController.abort();
      this.abortController = null;
      this.sending = false;
      this.pendingUserMsg = null;
      this.hitlMsgId = null;
    },

    /** 最后一条用户消息（无则 null）。 */
    findLastUserMsg(): ChatMsg | null {
      for (let i = this.messages.length - 1; i >= 0; i--) {
        if (this.messages[i].role === "user") return this.messages[i];
      }
      return null;
    },

    /** 统一构建 chat/stream 请求 payload。
     * 未显式传开关时，默认取当前 chatOptions 开关状态（而非硬编码 true），
     * 确保建议按钮（ask→send(q)）等未传 opts 的路径也遵守用户关闭的开关，
     * 否则关闭联网后点建议按钮仍会带 use_search=true。 */
    _buildPayload(
      sessionId: string,
      message: string,
      opts: {
        useRag?: boolean;
        useSearch?: boolean;
        useMemory?: boolean;
        resume?: "confirmed" | "cancelled";
        checkpointId?: string;
      } = {},
    ): Record<string, unknown> {
      const options = useChatOptionsStore();
      const payload: Record<string, unknown> = {
        session_id: sessionId,
        message,
        use_rag: opts.useRag ?? options.useRag,
        use_search: opts.useSearch ?? options.useSearch,
        use_memory: opts.useMemory ?? options.useMemory,
      };
      if (opts.resume) payload.resume = opts.resume;
      if (opts.checkpointId) payload.checkpoint_id = opts.checkpointId;
      return payload;
    },

    /** 发送消息（SSE 流式）。fromModal 用于 Time Travel 分叉等程序化发送。 */
    async send(text: string, opts: SendOptions = {}) {
      if (this.sending || !text.trim()) return;
      const sessions = useSessionsStore();
      const payload = this._buildPayload(sessions.currentId, text, opts);

      this.messages.push({
        id: nid(),
        role: "user",
        content: text,
        createdAt: new Date().toISOString(),
      });
      // 记录待回填后端 id 的用户消息（SSE meta 帧到达时写入 backendId）
      const userMsg = this.messages[this.messages.length - 1];
      this.pendingUserMsg = userMsg;
      // reactive 包装：_handleEvent 通过闭包修改该对象时能触发渲染
      const agentMsg: ChatMsg = reactive({
        id: nid(),
        role: "assistant",
        content: "",
        streaming: true,
        orbit: [],
        hitl: null,
        createdAt: new Date().toISOString(),
      });
      this.messages.push(agentMsg);
      this.hitlMsgId = null;

      await this._runStream(payload, agentMsg);
      this.pendingUserMsg = null;
    },

    /** HITL：确认/取消后在同一气泡/轨道内继续（不新建气泡） */
    async resume(choice: "confirmed" | "cancelled", sessionId: string) {
      if (this.sending) return;
      const userMsg = this.findLastUserMsg();
      if (!userMsg) return;
      // 复用 interrupt 时的消息（仍在 DOM/列表里）
      const agentMsg = this.hitlMsgId ? this.messages.find((m) => m.id === this.hitlMsgId) : null;
      const target: ChatMsg =
        agentMsg ||
        reactive({
          id: nid(),
          role: "assistant",
          content: "",
          streaming: true,
          orbit: [],
          hitl: null,
          createdAt: new Date().toISOString(),
        });
      if (!agentMsg) this.messages.push(target);
      target.hitl = null;
      target.streaming = true;
      this.hitlMsgId = null;

      const payload = this._buildPayload(sessionId, userMsg.content, {
        resume: choice,
      });
      await this._runStream(payload, target);
    },

    /** 停止生成 */
    stop() {
      this.abortController?.abort();
    },

    /** 删除消息：调用后端删除后本地移除（无后端 id 时仅本地移除）。 */
    async deleteMessage(msg: ChatMsg) {
      const sessions = useSessionsStore();
      const idx = this.messages.findIndex((m) => m.id === msg.id);
      if (idx < 0) return;
      if (msg.backendId && sessions.currentId) {
        try {
          await sessionsApi.deleteMessage(sessions.currentId, msg.backendId);
        } catch (e) {
          await useDialogStore().alert(`删除失败：${(e as Error).message}`);
          return;
        }
      }
      this.messages.splice(idx, 1);
      if (this.hitlMsgId === msg.id) this.hitlMsgId = null;
    },

    /** 重试：从该助手消息对应的用户问题起原子截断（含该问题），再重新发送它 */
    async retry(agentMsg: ChatMsg) {
      if (this.sending) return;
      const idx = this.messages.findIndex((m) => m.id === agentMsg.id);
      if (idx < 0) return;
      // 向前找最近的一条用户消息作为问题
      let userMsg: ChatMsg | null = null;
      for (let i = idx - 1; i >= 0; i--) {
        if (this.messages[i].role === "user") {
          userMsg = this.messages[i];
          break;
        }
      }
      if (!userMsg) return;
      // 从该用户消息起截断（含其后的所有消息），重新发送
      if (!(await this._truncateFrom(userMsg))) return;
      await this.send(userMsg.content);
    },

    /** 编辑重发：原子截断该消息及其之后的历史，再用新文本重新发送 */
    async editAndResend(userMsg: ChatMsg, newText: string) {
      if (this.sending) return;
      const text = newText.trim();
      if (!text) return;
      const start = this.messages.indexOf(userMsg);
      if (start < 0) return;
      if (!(await this._truncateFrom(userMsg))) return;
      await this.send(text);
    },

    /**
     * 从某条消息起截断（含该条）：服务端一个事务删完，再同步本地列表。
     * 失败返回 false（调用方应中止重发），错误写入 historyError 由界面提示。
     */
    async _truncateFrom(userMsg: ChatMsg): Promise<boolean> {
      const sessionId = useSessionsStore().currentId;
      if (sessionId && userMsg.backendId) {
        try {
          await sessionsApi.truncate(sessionId, userMsg.backendId);
        } catch (e) {
          this.historyError = `截断历史失败：${(e as Error).message}`;
          return false;
        }
      }
      const start = this.messages.indexOf(userMsg);
      if (start >= 0) this.messages.splice(start, this.messages.length - start);
      this.hitlMsgId = null;
      return true;
    },

    async _runStream(payload: Record<string, unknown>, agentMsg: ChatMsg) {
      this.sending = true;
      const controller = new AbortController();
      this.abortController = controller;
      try {
        await streamChat(
          payload as never,
          (ev: SSEEvent) => this._handleEvent(ev, agentMsg),
          controller.signal,
        );
      } catch (e: unknown) {
        if ((e as Error).name === "AbortError") {
          agentMsg.content += "\n\n> ⏹ 已停止生成。";
        } else {
          agentMsg.content += `\n\n> 处理失败：${(e as Error).message}`;
        }
      } finally {
        agentMsg.streaming = false;
        // 回答时间以完成为准（开始时写入的只是占位，完成后覆盖）
        agentMsg.createdAt = new Date().toISOString();
        if (this.abortController === controller) {
          this.sending = false;
          this.abortController = null;
          // 若消息仍处于 HITL 待确认状态，保留 hitlMsgId 供 resume 在同一气泡继续
          if (!agentMsg.hitl) this.hitlMsgId = null;
        }
      }
    },

    _handleEvent(ev: SSEEvent, agentMsg: ChatMsg) {
      switch (ev.type) {
        case "token":
          agentMsg.content += ev.content;
          // 工具执行完成（开始输出答案）：停止轨道节点闪烁
          if (agentMsg.orbit?.some((n) => n.active)) {
            agentMsg.orbit.forEach((n) => (n.active = false));
          }
          break;
        case "message": {
          if (ev.data?.session_id) {
            const sessions = useSessionsStore();
            sessions.currentId = ev.data.session_id;
            sessions.load(); // 刷新标题（首条用户消息生成标题）
          }
          agentMsg.content = ev.content;
          if (ev.data?.message_id) agentMsg.backendId = ev.data.message_id as string;
          break;
        }
        case "meta": {
          // 回填用户消息的后端 id（供删除）
          if (this.pendingUserMsg && ev.data?.user_message_id) {
            this.pendingUserMsg.backendId = ev.data.user_message_id as string;
          }
          break;
        }
        case "interrupt": {
          agentMsg.hitl = {
            question: ev.content,
            sessionId: (ev.data?.session_id as string) || useSessionsStore().currentId,
          };
          this.hitlMsgId = agentMsg.id;
          break;
        }
        default: {
          // start/tool/end/error → 轨道节点（start 只在轨道为空时追加一次）
          const t = ev.type as OrbitType;
          if (!["start", "tool", "end", "error"].includes(t)) return;
          if (!agentMsg.orbit) agentMsg.orbit = [];
          if (t === "start" && agentMsg.orbit.length) return;
          const label = orbitLabel(t, ev.content);
          // 引用溯源：来源挂在 tool 事件上，必须先取出来——后端对同一工具会先发
          // agent("调用 xxx") 再发 tool("工具: xxx")，后者会被下面的同标签去重跳过，
          // 若把赋值写在去重之后，来源就会丢。
          if (t === "tool" && Array.isArray(ev.data?.sources) && ev.data.sources.length) {
            agentMsg.sources = normalizeSources(ev.data.sources);
          }
          // 同标签去重：后端对同一工具会推送 agent("调用 xxx") + tool("工具: xxx")，
          // 两者解析出的轨道标签相同，避免轨道出现重复节点
          if ((t === "agent" || t === "tool") && agentMsg.orbit.some((n) => n.label === label))
            return;
          if (t === "tool") {
            // 新工具调用：前一个停止闪烁，当前节点开始闪烁（执行中）
            agentMsg.orbit.forEach((n) => (n.active = false));
            agentMsg.orbit.push({ type: t, label, active: true });
          } else {
            agentMsg.orbit.push({ type: t, label });
          }
        }
      }
    },
  },
});
