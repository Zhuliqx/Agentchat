import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("@/api", () => ({
  memoryApi: {
    list: vi.fn(async () => []),
    add: vi.fn(async () => ({})),
    remove: vi.fn(async () => undefined),
  },
}));

import { memoryApi } from "@/api";
import MemoryPanel from "@/components/sidebar/MemoryPanel.vue";
import { useDialogStore } from "@/stores/dialog";
import { useMemoryStore } from "@/stores/memory";

describe("MemoryPanel 失败提示", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("保存失败时提示原因", async () => {
    vi.mocked(memoryApi.add).mockRejectedValueOnce(new Error("后端不可达"));
    const wrapper = mount(MemoryPanel, { global: { stubs: { teleport: true } } });
    const ui = useDialogStore();

    await wrapper.find("input[placeholder='记住一条信息…']").setValue("新记忆");
    await wrapper.find("button[title='添加记忆']").trigger("click");
    await flushPromises();

    expect(ui.current?.kind).toBe("alert");
    expect(ui.current?.message).toContain("保存记忆失败");
    expect(ui.current?.message).toContain("后端不可达");
    ui.resolve(undefined);
    wrapper.unmount();
  });

  it("删除失败时提示原因", async () => {
    const memory = useMemoryStore();
    memory.list = [{ id: "m1", user_id: "u", content: "旧记忆", created_at: "", updated_at: "" }];
    vi.mocked(memoryApi.remove).mockRejectedValueOnce(new Error("后端不可达"));
    const wrapper = mount(MemoryPanel, { global: { stubs: { teleport: true } } });
    const ui = useDialogStore();

    await wrapper.find("button[title='删除记忆']").trigger("click");
    await flushPromises();

    expect(ui.current?.kind).toBe("alert");
    expect(ui.current?.message).toContain("删除记忆失败");
    ui.resolve(undefined);
    wrapper.unmount();
  });
});
