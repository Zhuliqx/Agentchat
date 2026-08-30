import { describe, expect, it, vi, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useTaskAgentStore } from "@/stores/taskAgent";

// mock api 层（避免真实网络 / SSE）
vi.mock("@/api", () => ({
  agentTasksApi: {
    runStream: vi.fn(),
    confirm: vi.fn(),
    history: vi.fn(),
    run: vi.fn(),
  },
}));

import { agentTasksApi } from "@/api";
import type { AgentTaskFrame } from "@/types/api";

const runStream = agentTasksApi.runStream as unknown as ReturnType<typeof vi.fn>;
const confirmApi = agentTasksApi.confirm as unknown as ReturnType<typeof vi.fn>;
const historyApi = agentTasksApi.history as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

function emitFrames(frames: AgentTaskFrame[]) {
  runStream.mockImplementation(
    async (_body: unknown, onFrame: (f: AgentTaskFrame) => void) => {
      for (const f of frames) await onFrame(f);
    }
  );
}

describe("taskAgent store", () => {
  it("runs a task via SSE and applies the result", async () => {
    const store = useTaskAgentStore();
    emitFrames([
      { type: "event", kind: "plan", data: { subtasks: 2 } },
      { type: "event", kind: "execute", data: { subtask: "调研", ok: true } },
      {
        type: "result",
        data: {
          session_id: "task-abc",
          status: "done",
          plan: "计划内容",
          findings: ["结论 1", "结论 2"],
          final_answer: "最终答案",
        },
      },
    ]);
    await store.run("做一个调研");

    expect(runStream).toHaveBeenCalledWith(
      { goal: "做一个调研" },
      expect.any(Function),
      expect.any(AbortSignal)
    );
    expect(store.status).toBe("done");
    expect(store.sessionId).toBe("task-abc");
    expect(store.plan).toBe("计划内容");
    expect(store.findings).toEqual(["结论 1", "结论 2"]);
    expect(store.finalAnswer).toBe("最终答案");
    expect(store.events.map((e) => e.kind)).toEqual(["plan", "execute"]);
  });

  it("enters awaiting_confirm on hitl event and confirm() resumes", async () => {
    const store = useTaskAgentStore();
    emitFrames([
      { type: "event", kind: "plan", data: { subtasks: 1 } },
      {
        type: "event",
        kind: "hitl",
        data: { next_action: "查数据库", expected_source: "db", step: 1 },
      },
      {
        type: "result",
        data: {
          session_id: "task-abc",
          status: "awaiting_confirm",
          plan: null,
          findings: [],
          final_answer: "",
          pending: { next_action: "查数据库", expected_source: "db", step: 1 },
        },
      },
    ]);
    await store.run("统计会话数");

    expect(store.status).toBe("awaiting_confirm");
    expect(store.pending?.next_action).toBe("查数据库");
    expect(store.pending?.expected_source).toBe("db");

    confirmApi.mockResolvedValue({
      session_id: "task-abc",
      status: "done",
      plan: null,
      findings: [],
      final_answer: "答案是 42",
    });
    await store.confirm("proceed");

    expect(confirmApi).toHaveBeenCalledWith({
      session_id: "task-abc",
      verb: "proceed",
      action: undefined,
      source: undefined,
    });
    expect(store.status).toBe("done");
    expect(store.finalAnswer).toBe("答案是 42");
    expect(store.pending).toBeNull();
  });

  it("stop() aborts and marks as stopped", async () => {
    const store = useTaskAgentStore();
    runStream.mockImplementation(
      (_body: unknown, _onFrame: unknown, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
          });
        })
    );
    const p = store.run("长任务");
    expect(store.status).toBe("running");
    store.stop();
    expect(store.abortController?.signal.aborted).toBe(true);
    await p;
    expect(store.error).toBe("已停止");
    expect(store.status).toBe("idle");
  });

  it("fork() sends checkpoint body and loadHistory() fills history", async () => {
    const store = useTaskAgentStore();
    store.sessionId = "task-abc";
    emitFrames([
      {
        type: "result",
        data: { session_id: "task-abc", status: "done", plan: null, findings: [], final_answer: "x" },
      },
    ]);
    await store.fork("cp-1", "", "新目标");

    expect(runStream).toHaveBeenCalledWith(
      {
        goal: "新目标",
        session_id: "task-abc",
        checkpoint_id: "cp-1",
        checkpoint_ns: "",
      },
      expect.any(Function),
      expect.any(AbortSignal)
    );

    historyApi.mockResolvedValue({
      session_id: "task-abc",
      history: [{ checkpoint_id: "cp-1", created_at: "", next: [], summary: "旧", interrupted: false }],
    });
    await store.loadHistory();
    expect(store.history).toHaveLength(1);
    expect(store.history[0].checkpoint_id).toBe("cp-1");
  });
});
