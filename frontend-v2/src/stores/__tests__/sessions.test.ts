import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

vi.mock("@/api", () => ({
  sessionsApi: {
    list: vi.fn(async () => []),
    remove: vi.fn(async () => undefined),
    pin: vi.fn(async (id: string, pinned: boolean) => ({
      id,
      title: id,
      pinned,
      created_at: "",
      updated_at: "",
    })),
  },
  streamChat: vi.fn(),
}));

import { sessionsApi } from "@/api";
import { useSessionsStore } from "@/stores/sessions";

const mk = (id: string) => ({ id, title: id, created_at: "", updated_at: "" });

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("会话列表", () => {
  it("load() 一次拉取全部会话（侧栏不设显示上限）", async () => {
    vi.mocked(sessionsApi.list).mockResolvedValueOnce([mk("s1"), mk("s2"), mk("s3")]);

    const store = useSessionsStore();
    await store.load();

    expect(sessionsApi.list).toHaveBeenCalledTimes(1);
    expect(store.list.map((s) => s.id)).toEqual(["s1", "s2", "s3"]);
    expect(store.error).toBeNull();
  });

  it("加载失败时保留旧列表并记录原因", async () => {
    const store = useSessionsStore();
    store.list = [mk("s1")];
    vi.mocked(sessionsApi.list).mockRejectedValueOnce(new Error("后端不可达"));

    await store.load();

    expect(store.list.map((s) => s.id)).toEqual(["s1"]);
    expect(store.error).toBe("后端不可达");
  });

  it("删除会话后本地列表移除该条", async () => {
    const store = useSessionsStore();
    store.list = [mk("s1"), mk("s2")];

    await store.remove("s1");

    expect(store.list.map((s) => s.id)).toEqual(["s2"]);
  });

  it("取消置顶后按更新时间回到原位（与服务端同一排序口径）", async () => {
    const store = useSessionsStore();
    store.list = [
      { id: "s1", title: "旧", created_at: "", updated_at: "2026-09-01T00:00:00Z" },
      { id: "s2", title: "新", created_at: "", updated_at: "2026-09-10T00:00:00Z" },
      { id: "s3", title: "中", created_at: "", updated_at: "2026-09-05T00:00:00Z" },
    ];

    await store.pin("s1", true); // 置顶 → 排到最前
    expect(store.list.map((s) => s.id)).toEqual(["s1", "s2", "s3"]);

    await store.pin("s1", false); // 取消置顶 → 回到按更新时间的位置
    expect(store.list.map((s) => s.id)).toEqual(["s2", "s3", "s1"]);
  });
});
