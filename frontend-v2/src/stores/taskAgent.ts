import { defineStore } from "pinia";
import { agentTasksApi } from "@/api";
import type {
  AgentTaskFrame,
  AgentTaskHistoryItem,
  AgentTaskPending,
  AgentTaskResult,
  AgentTaskRunBody,
} from "@/types/api";

export type TaskAgentStatus =
  | "idle"
  | "running"
  | "awaiting_confirm"
  | "done"
  | "error";

export interface TaskTraceItem {
  id: number;
  kind: string;
  label: string;
  detail: string;
  ok?: boolean;
  time: string;
}

const KIND_LABEL: Record<string, string> = {
  plan: "开始规划",
  replan: "重新规划",
  execute: "执行",
  check: "完成度检查",
  verify: "自检",
  hitl: "人工确认",
  final: "生成最终答案",
};

let traceId = 0;

function traceFrom(
  kind: string,
  data: Record<string, unknown>
): { label: string; detail: string; ok?: boolean } {
  const label = KIND_LABEL[kind] || kind;
  switch (kind) {
    case "plan":
      // fixed 模式：plan_node 发 {subtasks, fallback}；replan 模式首次进入发 {action, source}
      if (typeof data.subtasks === "number") {
        return {
          label,
          detail: `拆分为 ${data.subtasks} 个子任务${
            data.fallback ? "（直答降级）" : ""
          }`,
        };
      }
      return {
        label,
        detail: String(data.action || ""),
      };
    case "execute":
      return { label, detail: String(data.subtask || data.action || ""), ok: !!data.ok };
    case "verify":
      return {
        label,
        detail: data.retry ? `判定重试（第 ${data.retries ?? 1} 次）` : "通过",
      };
    case "hitl":
      return { label, detail: String(data.next_action || "") };
    case "check":
      return { label, detail: data.ok ? "已完成" : "继续执行" };
    case "replan":
      return { label, detail: String(data.action || data.source || "") };
    case "final":
      return { label, detail: "" };
    default:
      return { label, detail: "" };
  }
}

/** 自主任务 Agent store：run/run-stream + HITL confirm + Time Travel（history/分叉/重放）。 */
export const useTaskAgentStore = defineStore("taskAgent", {
  state: () => ({
    open: false,
    status: "idle" as TaskAgentStatus,
    sessionId: "",
    plan: "",
    findings: [] as string[],
    finalAnswer: "",
    pending: null as AgentTaskPending | null,
    events: [] as TaskTraceItem[],
    history: [] as AgentTaskHistoryItem[],
    error: "",
    abortController: null as AbortController | null,
  }),
  getters: {
    running: (s) => s.status === "running",
  },
  actions: {
    reset() {
      this.status = "idle";
      this.sessionId = "";
      this.plan = "";
      this.findings = [];
      this.finalAnswer = "";
      this.pending = null;
      this.events = [];
      this.history = [];
      this.error = "";
    },
    openModal() {
      this.open = true;
    },
    closeModal() {
      this.open = false;
    },

    /** 统一启动：新任务 / Time Travel 分叉 / 重放共用一条流式通道。 */
    async start(body: AgentTaskRunBody) {
      if (this.running) return;
      this.reset();
      if (body.session_id) this.sessionId = body.session_id;
      this.status = "running";
      const controller = new AbortController();
      this.abortController = controller;
      try {
        await agentTasksApi.runStream(
          body,
          (frame) => this._onFrame(frame),
          controller.signal
        );
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          this.error = "已停止";
          this.status = "idle";
        } else {
          this.error = (e as Error).message;
          this.status = "error";
        }
      } finally {
        this.abortController = null;
        // SSE 连接断开但未收到 result/error 帧（如服务重启）时，避免卡在“执行中”
        if (this.status === "running") {
          this.status = "idle";
          this.error = "连接已断开，任务结果未知";
        }
      }
    },

    run(goal: string) {
      return this.start({ goal });
    },
    fork(checkpointId: string, checkpointNs: string, goal: string) {
      return this.start({
        goal,
        session_id: this.sessionId,
        checkpoint_id: checkpointId,
        checkpoint_ns: checkpointNs,
      });
    },
    replay(checkpointId: string, checkpointNs: string) {
      return this.start({
        goal: "",
        session_id: this.sessionId,
        checkpoint_id: checkpointId,
        checkpoint_ns: checkpointNs,
      });
    },

    async confirm(
      verb: "proceed" | "edit" | "skip",
      action?: string,
      source?: string
    ) {
      if (!this.sessionId || this.running) return;
      this.status = "running";
      const controller = new AbortController();
      this.abortController = controller;
      try {
        // 走 SSE：恢复后的事件（执行/自检/完成度/下一轮 HITL）实时进轨迹，最后统一收 result
        await agentTasksApi.confirmStream(
          { session_id: this.sessionId, verb, action, source },
          (frame) => this._onFrame(frame),
          controller.signal
        );
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          this.error = "已停止";
          this.status = "idle";
        } else {
          this.error = (e as Error).message;
          this.status = "error";
        }
      } finally {
        this.abortController = null;
        if (this.status === "running") {
          this.status = "idle";
          this.error = "连接已断开，任务结果未知";
        }
      }
    },

    stop() {
      this.abortController?.abort();
    },

    async loadHistory() {
      if (!this.sessionId) return;
      try {
        const r = await agentTasksApi.history(this.sessionId);
        this.history = r.history;
      } catch (e) {
        this.error = (e as Error).message;
      }
    },

    applyResult(r: AgentTaskResult) {
      this.sessionId = r.session_id;
      this.plan = r.plan || "";
      this.findings = r.findings || [];
      this.finalAnswer = r.final_answer || "";
      if (r.status === "awaiting_confirm") {
        this.status = "awaiting_confirm";
        this.pending = r.pending || this.pending;
      } else {
        this.status = "done";
        this.pending = null;
      }
    },

    _onFrame(frame: AgentTaskFrame) {
      if (frame.type === "event" && frame.kind) {
        if (frame.kind === "hitl") {
          // hitl 事件到达即可渲染确认卡片（result 帧会再次确认状态）。
          // LangGraph 恢复时会重放 interrupt 前的代码，同一中断的 hitl 事件会重复到达：
          // 与当前 pending 相同则视为重复（避免轨迹出现两条“人工确认”/状态抖动）。
          const next = String(frame.data.next_action || "");
          const src = String(frame.data.expected_source || "default");
          const dup =
            this.pending?.next_action === next &&
            this.pending?.expected_source === src;
          if (!dup) {
            this.pending = {
              next_action: next,
              expected_source: src,
              step: Number(frame.data.step || 0),
            };
            this.status = "awaiting_confirm";
            this.pushTrace(frame.kind, frame.data || {});
          }
          return;
        }
        this.pushTrace(frame.kind, frame.data || {});
      } else if (frame.type === "result") {
        this.applyResult(frame.data as unknown as AgentTaskResult);
      } else if (frame.type === "error") {
        this.error = String(frame.data?.message || "任务执行失败");
        this.status = "error";
      }
    },

    pushTrace(kind: string, data: Record<string, unknown>) {
      const t = traceFrom(kind, data);
      this.events.push({
        id: traceId++,
        kind,
        label: t.label,
        detail: t.detail,
        ok: t.ok,
        time: new Date().toLocaleTimeString(),
      });
    },
  },
});
