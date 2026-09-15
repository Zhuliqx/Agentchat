import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import StatsModal from "@/components/dialogs/StatsModal.vue";
import TasksModal from "@/components/dialogs/TasksModal.vue";
import ProfileModal from "@/components/dialogs/ProfileModal.vue";
import AdminModal from "@/components/dialogs/AdminModal.vue";
import { adminApi, authApi, sessionsApi, tasksApi } from "@/api";
import { useSessionsStore } from "@/stores/sessions";

vi.mock("@/api", () => ({
  sessionsApi: {
    stats: vi.fn(async () => ({
      session_id: "s1",
      message_count: 4,
      user_count: 2,
      assistant_count: 2,
      system_count: 0,
      rounds: 2,
      total_chars: 300,
      est_tokens: 120,
      avg_user_chars: 10,
      avg_assistant_chars: 200,
      longest_response_chars: 220,
      first_at: "2026-09-13T00:00:00Z",
      last_at: "2026-09-13T00:10:00Z",
      duration_sec: 600,
      avg_response_sec: 12.5,
      hourly_counts: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
      top_sources: [{ path: "data/kb/company.md", messages: 2, hits: 3 }],
    })),
  },
  authApi: {
    me: vi.fn(async () => ({
      id: "u1",
      username: "tester",
      created_at: "2026-09-01T00:00:00Z",
      avatar_color: "accent",
    })),
    stats: vi.fn(async () => ({
      username: "tester",
      created_at: "2026-09-01T00:00:00Z",
      session_count: 1,
      message_count: 2,
      memory_count: 0,
      document_count: 0,
      token_estimate: 10,
    })),
  },
  adminApi: {
    stats: vi.fn(async () => ({
      user_count: 1,
      session_count: 1,
      message_count: 2,
      document_count: 0,
    })),
    users: vi.fn(async () => []),
    usage: vi.fn(async () => ({ items: [], total_messages: 0, total_tokens: 0 })),
    settings: vi.fn(async () => ({ items: [] })),
    eval: vi.fn(async () => ({ docs: [], builtin: [], auto: [], custom: [] })),
  },
  tasksApi: {
    list: vi.fn(async () => []),
    registry: vi.fn(async () => [{ type: "cleanup", label: "清理", desc: "清理过期数据" }]),
  },
}));

const statsApi = sessionsApi.stats as unknown as ReturnType<typeof vi.fn>;
const meApi = authApi.me as unknown as ReturnType<typeof vi.fn>;
const authStatsApi = authApi.stats as unknown as ReturnType<typeof vi.fn>;
const adminStatsApi = adminApi.stats as unknown as ReturnType<typeof vi.fn>;
const taskListApi = tasksApi.list as unknown as ReturnType<typeof vi.fn>;

function mountDialog(component: Parameters<typeof mount>[0], pinia = createPinia()) {
  return mount(component, {
    props: { modelValue: true },
    global: { plugins: [pinia], stubs: { teleport: true } },
  });
}

describe("弹窗挂载即打开时加载数据", () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    vi.clearAllMocks();
    pinia = createPinia();
    setActivePinia(pinia);
  });

  it("会话数据分析拉取当前会话统计", async () => {
    useSessionsStore().currentId = "s1";
    const wrapper = mountDialog(StatsModal, pinia);
    await flushPromises();

    expect(statsApi).toHaveBeenCalledWith("s1");
    // 概览 + 三个可视化区块都渲染出来（旧版只有四张卡 + 一张表）
    expect(wrapper.text()).toContain("消息总数");
    expect(wrapper.text()).toContain("消息构成");
    expect(wrapper.text()).toContain("活跃时段");
    expect(wrapper.text()).toContain("引用来源 Top 1");
    expect(wrapper.text()).toContain("company.md");
    expect(wrapper.text()).toContain("平均应答");
    // 环形图：底环 + 用户/助手两段
    expect(wrapper.findAll("[data-testid='stats-composition'] circle")).toHaveLength(3);
    // 活跃时段：24 根柱子
    expect(wrapper.findAll("[data-testid='stats-hourly'] span[title]")).toHaveLength(24);
  });

  it("定时任务拉取列表与类型注册表", async () => {
    const wrapper = mountDialog(TasksModal, pinia);
    await flushPromises();

    expect(taskListApi).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("暂无任务");
  });

  it("个人主页拉取当前用户与统计", async () => {
    const wrapper = mountDialog(ProfileModal, pinia);
    await flushPromises();

    expect(meApi).toHaveBeenCalledTimes(1);
    expect(authStatsApi).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("tester");
  });

  it("管理后台拉取平台统计", async () => {
    mountDialog(AdminModal, pinia);
    await flushPromises();

    expect(adminStatsApi).toHaveBeenCalledTimes(1);
  });
});
