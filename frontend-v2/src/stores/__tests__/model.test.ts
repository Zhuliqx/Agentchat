import { describe, expect, it, vi, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useModelStore } from "@/stores/model";
import type { ModelListResponse } from "@/types/api";

vi.mock("@/api", () => ({
  modelsApi: {
    list: vi.fn(),
    setCurrent: vi.fn(),
  },
}));

import { modelsApi } from "@/api";

const RESP: ModelListResponse = {
  models: [
    {
      id: "deepseek:deepseek-chat",
      provider: "deepseek",
      model: "deepseek-chat",
      label: "DeepSeek Chat（deepseek-chat）",
    },
    {
      id: "dashscope:qwen-max",
      provider: "dashscope",
      model: "qwen-max",
      label: "通义 qwen-max（更强推理）",
    },
  ],
  current: { provider: "dashscope", model: "qwen-max" },
};

function mockApi(m: { list?: unknown; setCurrent?: unknown }) {
  (modelsApi.list as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
    m.list ?? RESP
  );
  (modelsApi.setCurrent as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
    m.setCurrent ?? RESP
  );
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("model store", () => {
  it("load() resolves currentId from backend current", async () => {
    mockApi({});
    const store = useModelStore();
    await store.load();
    expect(store.models.length).toBe(2);
    expect(store.currentId).toBe("dashscope:qwen-max");
    expect(store.currentShort).toBe("qwen-max");
    expect(store.currentLabel).toContain("qwen-max");
  });

  it("load() leaves currentId empty when no current", async () => {
    mockApi({ list: { ...RESP, current: null } });
    const store = useModelStore();
    await store.load();
    expect(store.currentId).toBe("");
    expect(store.currentShort).toBe("");
  });

  it("set() updates current and rolls back on failure", async () => {
    mockApi({});
    const store = useModelStore();
    await store.load();

    mockApi({
      setCurrent: {
        ...RESP,
        current: { provider: "deepseek", model: "deepseek-chat" },
      },
    });
    await store.set("deepseek:deepseek-chat");
    expect(store.currentId).toBe("deepseek:deepseek-chat");

    // 切换失败 → 回滚到之前的选择
    (modelsApi.setCurrent as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail")
    );
    await store.set("dashscope:qwen-max");
    expect(store.currentId).toBe("deepseek:deepseek-chat");
  });
});
