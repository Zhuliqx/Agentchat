import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick, reactive } from "vue";
import MessageItem from "@/components/chat/MessageItem.vue";
import { useChatStore, type ChatMsg } from "@/stores/chat";

vi.mock("@/api", () => ({
  docsApi: {
    fileUrl: (source: string, download = false) =>
      `/file?source=${source}${download ? "&dl=1" : ""}`,
    preview: vi.fn(async () => ({ text: "# 文件内容\n第一行", binary: false })),
  },
}));

import { docsApi } from "@/api";

describe("MessageItem streaming render", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the final content when streaming ends within the throttle window", async () => {
    const msg = reactive<ChatMsg>({
      id: "m1",
      role: "assistant",
      content: "",
      streaming: true,
    });
    const wrapper = mount(MessageItem, { props: { msg } });

    msg.content = "中间内容";
    await nextTick();
    msg.content = "最终答案";
    msg.streaming = false;
    await nextTick();
    vi.runAllTimers();
    await nextTick();

    expect(wrapper.text()).toContain("最终答案");
    wrapper.unmount();
  });

  it("助手操作条带 hover 显隐类，并显示消息时间", async () => {
    const msg = reactive<ChatMsg>({
      id: "m2",
      role: "assistant",
      content: "答案",
      createdAt: "2026-09-13T06:32:00.000Z",
    });
    const wrapper = mount(MessageItem, { props: { msg } });

    const actions = wrapper.find(".msg-actions");
    expect(actions.exists()).toBe(true);
    // 时间戳（按本地时区渲染，这里只断言格式 HH:MM）
    expect(actions.text()).toMatch(/\d{2}:\d{2}/);
    wrapper.unmount();
  });

  it("用户消息的操作条在气泡下方，含时间、复制与编辑", () => {
    const msg = reactive<ChatMsg>({
      id: "m3",
      role: "user",
      content: "问题",
      createdAt: "2026-09-13T06:32:00.000Z",
    });
    const wrapper = mount(MessageItem, { props: { msg } });

    const actions = wrapper.find(".msg-actions");
    expect(actions.exists()).toBe(true);
    expect(actions.classes()).toContain("justify-end"); // 右对齐在气泡下方
    expect(actions.text()).toMatch(/\d{2}:\d{2}/);
    expect(actions.find('button[aria-label="复制"]').exists()).toBe(true);
    expect(actions.find('button[aria-label="编辑并重新发送"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("回答里的代码块与表格被增强（复制按钮 + 表格滚动容器）", async () => {
    const msg = reactive<ChatMsg>({
      id: "m7",
      role: "assistant",
      content: "示例：\n\n```python\nprint(1)\n```\n\n| 列 | 值 |\n| --- | --- |\n| a | 1 |",
    });
    const wrapper = mount(MessageItem, { props: { msg } });
    vi.runAllTimers(); // 渲染走 useThrottleFn，先放行节流
    await nextTick();
    await nextTick();

    expect(wrapper.find("[data-code-block]").exists()).toBe(true);
    expect(wrapper.find("[data-copy-code]").text()).toBe("复制");
    expect(wrapper.find(".md-table-wrap > table").exists()).toBe(true);
    wrapper.unmount();
  });

  it("来源 chips 显示数量与文件名，点击后在应用内预览", async () => {
    const preview = docsApi.preview as unknown as ReturnType<typeof vi.fn>;
    preview.mockClear();
    const msg = reactive<ChatMsg>({
      id: "m8",
      role: "assistant",
      content: "回答",
      sources: [
        { path: "D:\\桌面\\Agentchat\\data\\kb\\company.md", hits: 3 },
        { path: "/srv/data/policies.md" },
      ],
    });
    const wrapper = mount(MessageItem, {
      props: { msg },
      global: { stubs: { teleport: true } },
    });

    // 数量 + 只显示文件名（Windows 反斜杠路径也要正确取尾段）
    expect(wrapper.text()).toContain("来源 2");
    expect(wrapper.text()).toContain("company.md");
    expect(wrapper.text()).toContain("policies.md");
    // 序号即引用编号：正文里的 [n] 能直接对应到第 n 个 chip
    const chips = wrapper.findAll("a[data-source-index]");
    expect(chips.map((c) => c.attributes("data-source-index"))).toEqual(["1", "2"]);
    expect(chips.map((c) => c.text())).toEqual(["1 company.md ×3", "2 policies.md"]);
    // 命中多段的来源标出数量，命中 1 段的不标
    expect(wrapper.text()).toContain("×3");
    expect(wrapper.text()).not.toContain("×1");
    // 链接仍指向原始文件（允许中键/新标签打开）
    expect(wrapper.find('a[href*="company.md"]').exists()).toBe(true);

    await wrapper.find('a[href*="company.md"]').trigger("click");
    await nextTick();
    await nextTick();

    expect(preview).toHaveBeenCalledWith("D:\\桌面\\Agentchat\\data\\kb\\company.md");
    // teleport 被 stub，弹窗内容渲染在组件内
    expect(wrapper.text()).toContain("文件内容");
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("助手消息有后续内容时提供分支入口，且不再提供删除", async () => {
    const chat = useChatStore();
    const first = reactive<ChatMsg>({ id: "m9", role: "assistant", content: "第一条答案" });
    const second = reactive<ChatMsg>({ id: "m10", role: "user", content: "后续追问" });
    chat.messages = [first, second];

    const wrapper = mount(MessageItem, {
      props: { msg: first },
      global: { stubs: { teleport: true } },
    });

    expect(wrapper.find('[aria-label="删除消息"]').exists()).toBe(false);
    const branch = wrapper.find('[aria-label="从这里分支"]');
    expect(branch.exists()).toBe(true);

    await branch.trigger("click");
    expect(chat.branchFrom?.id).toBe("m9");
    wrapper.unmount();

    // 最后一条消息没有可删除的后续内容 → 不显示分支入口
    chat.cancelBranch();
    const lastWrapper = mount(MessageItem, {
      props: { msg: second },
      global: { stubs: { teleport: true } },
    });
    expect(lastWrapper.find('[aria-label="从这里分支"]').exists()).toBe(false);
    expect(lastWrapper.find('[aria-label="删除消息"]').exists()).toBe(false);
    lastWrapper.unmount();
  });

  it("用户消息也提供复制按钮，点击写入剪贴板", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const msg = reactive<ChatMsg>({ id: "m11", role: "user", content: "复制我" });

    const wrapper = mount(MessageItem, {
      props: { msg },
      global: { stubs: { teleport: true } },
    });

    const copy = wrapper.find('button[aria-label="复制"]');
    expect(copy.exists()).toBe(true);
    await copy.trigger("click");
    await nextTick();
    expect(writeText).toHaveBeenCalledWith("复制我");
    wrapper.unmount();
  });

  it("收到 ↑ 编辑请求时就地展开编辑框并聚焦", async () => {
    const chat = useChatStore();
    const msg = reactive<ChatMsg>({ id: "m12", role: "user", content: "改我" });
    chat.messages = [msg];
    const wrapper = mount(MessageItem, {
      props: { msg },
      global: { stubs: { teleport: true } },
    });
    expect(wrapper.find("textarea").exists()).toBe(false);

    chat.requestEdit(msg);
    await nextTick();

    const area = wrapper.find("textarea");
    expect(area.exists()).toBe(true);
    expect((area.element as HTMLTextAreaElement).value).toBe("改我");

    // Esc 退出编辑态（不保存）
    await area.trigger("keydown", { key: "Escape" });
    expect(wrapper.find("textarea").exists()).toBe(false);
    wrapper.unmount();
  });
});
