import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { sessionsApi } from "@/api";
import SessionList from "@/components/sidebar/SessionList.vue";
import { useChatStore } from "@/stores/chat";
import { useDialogStore } from "@/stores/dialog";
import { useSessionsStore } from "@/stores/sessions";

vi.mock("@/api", () => ({
  sessionsApi: {
    list: vi.fn(async () => []),
    history: vi.fn(async () => []),
    remove: vi.fn(async () => undefined),
    rename: vi.fn(),
    pin: vi.fn(),
  },
  streamChat: vi.fn(),
}));

describe("SessionList", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("clears the chat area when the current session is deleted", async () => {
    const sessions = useSessionsStore();
    sessions.list = [
      { id: "s1", title: "会话一", created_at: "", updated_at: "" },
      { id: "s2", title: "会话二", created_at: "", updated_at: "" },
    ];
    sessions.currentId = "s1";
    const chat = useChatStore();
    chat.messages = [{ id: "m1", role: "assistant", content: "旧会话内容" }];
    const wrapper = mount(SessionList);
    await wrapper.findAll('button[title="删除会话"]')[0].trigger("click");
    await flushPromises();
    useDialogStore().resolve(true);
    await flushPromises();

    expect(sessions.currentId).toBe("s2");
    expect(chat.messages).toEqual([]);
    expect(sessionsApi.history).toHaveBeenLastCalledWith("s2");
    wrapper.unmount();
  });

  it("列表行显示相对时间，便于区分同名会话", async () => {
    const sessions = useSessionsStore();
    sessions.list = [
      {
        id: "s1",
        title: "同名会话",
        created_at: "",
        updated_at: new Date(Date.now() - 3 * 60_000).toISOString(),
      },
    ];
    sessions.currentId = "s1";

    const wrapper = mount(SessionList);

    expect(wrapper.text()).toContain("3 分钟前");
    expect(wrapper.text()).toContain("同名会话");
    wrapper.unmount();
  });
});
