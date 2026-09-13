import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick, reactive } from "vue";
import MessageItem from "@/components/chat/MessageItem.vue";
import type { ChatMsg } from "@/stores/chat";

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

  it("用户消息在气泡下方显示时间，且带 hover 显隐类", () => {
    const msg = reactive<ChatMsg>({
      id: "m3",
      role: "user",
      content: "问题",
      createdAt: "2026-09-13T06:32:00.000Z",
    });
    const wrapper = mount(MessageItem, { props: { msg } });

    const stamp = wrapper.find(".msg-stamp");
    expect(stamp.exists()).toBe(true);
    expect(stamp.text()).toMatch(/\d{2}:\d{2}/);
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
});
