import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
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

  it("按 置顶/今天/昨天/更早 分组：置顶不混进更早，空组不渲染", () => {
    const sessions = useSessionsStore();
    const at = (daysAgo: number) => {
      const d = new Date();
      d.setDate(d.getDate() - daysAgo);
      d.setHours(10, 0, 0, 0);
      return d.toISOString();
    };
    sessions.list = [
      { id: "p1", title: "置顶的老会话", created_at: "", updated_at: at(9), pinned: true },
      { id: "t1", title: "今天的会话", created_at: "", updated_at: at(0) },
      { id: "y1", title: "昨天的会话", created_at: "", updated_at: at(1) },
      { id: "e1", title: "更早的会话", created_at: "", updated_at: at(6) },
    ];

    const wrapper = mount(SessionList);
    const text = wrapper.text();

    expect(text).toContain("置顶");
    expect(text).toContain("今天");
    expect(text).toContain("昨天");
    expect(text).toContain("更早");
    // 分组顺序：置顶 → 今天 → 昨天 → 更早
    expect(text.indexOf("置顶")).toBeLessThan(text.indexOf("今天"));
    expect(text.indexOf("今天")).toBeLessThan(text.indexOf("昨天"));
    expect(text.indexOf("昨天")).toBeLessThan(text.indexOf("更早"));
    // 置顶的会话属于"置顶"组，不因为时间久远掉进"更早"
    expect(text.indexOf("置顶的老会话")).toBeLessThan(text.indexOf("更早"));
    wrapper.unmount();
  });

  it("只有今天有会话时，不渲染其它分组标题", () => {
    const sessions = useSessionsStore();
    sessions.list = [
      { id: "t1", title: "今天的会话", created_at: "", updated_at: new Date().toISOString() },
    ];

    const wrapper = mount(SessionList);

    expect(wrapper.text()).toContain("今天");
    expect(wrapper.text()).not.toContain("昨天");
    expect(wrapper.text()).not.toContain("更早");
    expect(wrapper.text()).not.toContain("置顶");
    wrapper.unmount();
  });

  it("触屏长按弹操作表；抬手那一次点击被吞掉，不打开会话", async () => {
    vi.useFakeTimers();
    const sessions = useSessionsStore();
    const at = new Date().toISOString();
    sessions.list = [
      { id: "s1", title: "会话一", created_at: "", updated_at: at },
      { id: "s2", title: "会话二", created_at: "", updated_at: at },
    ];
    sessions.currentId = "s2";
    const wrapper = mount(SessionList, { global: { stubs: { teleport: true } } });
    const row = wrapper.findAll("button").find((b) => b.text().includes("会话一"))!;

    await row.trigger("pointerdown", { pointerType: "touch" });
    vi.advanceTimersByTime(500);
    await nextTick();

    const menu = wrapper.find('[role="menu"]');
    expect(menu.exists()).toBe(true);
    expect(menu.text()).toContain("置顶");
    expect(menu.text()).toContain("重命名");
    expect(menu.text()).toContain("删除");

    await row.trigger("click");
    expect(sessions.currentId).toBe("s2"); // 长按后的抬手不当作"打开会话"
    wrapper.unmount();
    vi.useRealTimers();
  });

  it("鼠标按下不触发长按（桌面走 hover 按钮）", async () => {
    vi.useFakeTimers();
    const sessions = useSessionsStore();
    sessions.list = [
      { id: "s1", title: "会话一", created_at: "", updated_at: new Date().toISOString() },
    ];
    const wrapper = mount(SessionList, { global: { stubs: { teleport: true } } });
    const row = wrapper.findAll("button").find((b) => b.text().includes("会话一"))!;

    await row.trigger("pointerdown", { pointerType: "mouse" });
    vi.advanceTimersByTime(500);
    await nextTick();

    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
    wrapper.unmount();
    vi.useRealTimers();
  });

  it("操作表里可以置顶 / 就地重命名", async () => {
    vi.useFakeTimers();
    const sessions = useSessionsStore();
    sessions.list = [
      { id: "s1", title: "会话一", created_at: "", updated_at: new Date().toISOString() },
    ];
    vi.mocked(sessionsApi.pin).mockResolvedValue({
      id: "s1",
      title: "会话一",
      created_at: "",
      updated_at: "",
      pinned: true,
    });
    const wrapper = mount(SessionList, { global: { stubs: { teleport: true } } });
    const row = wrapper.findAll("button").find((b) => b.text().includes("会话一"))!;

    const longPress = async () => {
      await row.trigger("pointerdown", { pointerType: "touch" });
      vi.advanceTimersByTime(500);
      await nextTick();
    };
    const pick = async (label: string) => {
      const item = wrapper.findAll('[role="menuitem"]').find((b) => b.text() === label)!;
      await item.trigger("click");
      await flushPromises();
    };

    await longPress();
    await pick("置顶");
    expect(sessionsApi.pin).toHaveBeenCalledWith("s1", true);

    await longPress();
    await pick("重命名");
    // 标题就地换成输入框，值为原标题
    const input = wrapper.find("input:not([type='checkbox'])");
    expect(input.exists()).toBe(true);
    expect((input.element as HTMLInputElement).value).toBe("会话一");

    wrapper.unmount();
    vi.useRealTimers();
  });
});
