import { defineStore } from "pinia";
import { reactive } from "vue";
import { sessionsApi, streamChat } from "@/api";
import { useSessionsStore } from "./sessions";
import type { Message, SSEEvent } from "@/types/api";

export type OrbitType = "start" | "tool" | "end" | "error";
export interface OrbitNode {
  type: OrbitType;
  label: string;
}

export interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string; // Markdown 原文（流式时逐步累积，组件节流渲染）
  streaming?: boolean;
  orbit?: OrbitNode[];
  hitl?: { question: string; sessionId: string } | null;
}

export interface SendOptions {
  useRag?: boolean;
  useSearch?: boolean;
  useMemory?: boolean;
  checkpointId?: string; // Time Travel 分叉起点
}

const ORBIT_META: Record<string, [string, string]> = {
  rag_agent: ["📚", "知识库"],
  mcp_agent: ["🗄", "数据库/工具"],
  web_search: ["🌐", "联网搜索"],
  search_agent: ["🌐", "联网搜索"], // 兼容旧事件（改名前的历史记录）
  recall_memory: ["🧠", "记忆"],
  remember_memory: ["🧠", "记忆"],
  request_confirmation: ["⚠️", "人工确认"],
};
const ORBIT_NAME_RE = new RegExp(Object.keys(ORBIT_META).join("|"));

let uid = 0;
const nid = () => `m${Date.now()}_${uid++}`;

function orbitLabel(type: OrbitType, text: string): string {
  if (type === "start") return "Supervisor";
  if (type === "end") return "完成";
  if (type === "error") return "错误";
  const m = String(text || "").match(ORBIT_NAME_RE);
  const name = m ? m[0] : null;
  if (name && ORBIT_META[name]) return `${name} · ${ORBIT_META[name][1]}`;
  return name || String(text || "").slice(0, 18);
}

export const useChatStore = defineStore("chat", {
  state: () => ({
    messages: [] as ChatMsg[],
    sending: false,
    abortController: null as AbortController | null,
    // HITL：interrupt 时记录所在消息 id，确认后在同一个气泡/轨道内继续
    hitlMsgId: null as string | null,
  }),
  getters: {
    lastAssistant: (s): ChatMsg | null => {
      for (let i = s.messages.length - 1; i >= 0; i--) {
        if (s.messages[i].role === "assistant") return s.messages[i];
      }
      return null;
    },
  },
  actions: {
    async loadHistory(sessionId: string) {
      this.messages = [];
      const msgs = await sessionsApi.history(sessionId);
      msgs.forEach((m: Message) => {
        this.messages.push({
          id: nid(),
          role: m.role === "user" ? "user" : "assistant",
          content: m.content,
        });
      });
    },
    clear() {
      this.messages = [];
      this.hitlMsgId = null;
    },

    /** 发送消息（SSE 流式）。fromModal 用于 Time Travel 分叉等程序化发送。 */
    async send(text: string, opts: SendOptions = {}) {
      if (this.sending || !text.trim()) return;
      const sessions = useSessionsStore();
      const payload: Record<string, unknown> = {
        session_id: sessions.currentId,
        message: text,
        use_rag: opts.useRag ?? true,
        use_search: opts.useSearch ?? true,
        use_memory: opts.useMemory ?? true,
      };
      if (opts.checkpointId) payload.checkpoint_id = opts.checkpointId;

      this.messages.push({ id: nid(), role: "user", content: text });
      // reactive 包装：_handleEvent 通过闭包修改该对象时能触发渲染
      const agentMsg: ChatMsg = reactive({
        id: nid(),
        role: "assistant",
        content: "",
        streaming: true,
        orbit: [],
        hitl: null,
      });
      this.messages.push(agentMsg);
      this.hitlMsgId = null;

      await this._runStream(payload, agentMsg);
    },

    /** HITL：确认/取消后在同一气泡/轨道内继续（不新建气泡） */
    async resume(choice: "confirmed" | "cancelled", sessionId: string) {
      if (this.sending) return;
      const pending = this.messages.find((m) => m.role === "user" && !m.streaming);
      const userMsg = [...this.messages].reverse().find((m) => m.role === "user");
      if (!userMsg) return;
      // 复用 interrupt 时的消息（仍在 DOM/列表里）
      const agentMsg = this.hitlMsgId
        ? this.messages.find((m) => m.id === this.hitlMsgId)
        : null;
      const target: ChatMsg =
        agentMsg ||
        reactive({ id: nid(), role: "assistant", content: "", streaming: true, orbit: [], hitl: null });
      if (!agentMsg) this.messages.push(target);
      target.hitl = null;
      target.streaming = true;
      this.hitlMsgId = null;

      const payload: Record<string, unknown> = {
        session_id: sessionId,
        message: userMsg.content,
        use_rag: true,
        use_search: true,
        resume: choice,
      };
      await this._runStream(payload, target);
    },

    /** 停止生成 */
    stop() {
      this.abortController?.abort();
    },

    /** 重试：删除该助手消息及其前面的用户问题，截断后续消息后重新发送该问题 */
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
      const start = this.messages.indexOf(userMsg);
      this.messages.splice(start, this.messages.length - start);
      this.hitlMsgId = null;
      await this.send(userMsg.content);
    },

    async _runStream(payload: Record<string, unknown>, agentMsg: ChatMsg) {
      this.sending = true;
      const controller = new AbortController();
      this.abortController = controller;
      try {
        await streamChat(
          payload as never,
          (ev: SSEEvent) => this._handleEvent(ev, agentMsg),
          controller.signal
        );
      } catch (e: unknown) {
        if ((e as Error).name === "AbortError") {
          agentMsg.content += "\n\n> ⏹ 已停止生成。";
        } else {
          agentMsg.content += `\n\n> 处理失败：${(e as Error).message}`;
        }
      } finally {
        agentMsg.streaming = false;
        this.sending = false;
        this.abortController = null;
        // 若消息仍处于 HITL 待确认状态，保留 hitlMsgId 供 resume 在同一气泡继续
        if (!agentMsg.hitl) this.hitlMsgId = null;
      }
    },

    _handleEvent(ev: SSEEvent, agentMsg: ChatMsg) {
      switch (ev.type) {
        case "token":
          agentMsg.content += ev.content;
          break;
        case "message": {
          if (ev.data?.session_id) {
            const sessions = useSessionsStore();
            sessions.currentId = ev.data.session_id;
            sessions.load(); // 刷新标题（首条用户消息生成标题）
          }
          agentMsg.content = ev.content;
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
          agentMsg.orbit.push({ type: t, label: orbitLabel(t, ev.content) });
        }
      }
    },
  },
});
