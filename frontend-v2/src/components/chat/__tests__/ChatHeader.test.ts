import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import ChatHeader from "@/components/chat/ChatHeader.vue";
import { useChatStore } from "@/stores/chat";
import { useSessionsStore } from "@/stores/sessions";

vi.mock("@/api", () => ({ sessionsApi: {}, authApi: {} }));

function mountHeader(props: Record<string, unknown> = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  return mount(ChatHeader, {
    props,
    global: { plugins: [pinia], stubs: { teleport: true } },
  });
}

describe("ChatHeader", () => {
  it("图标按会话操作 / 平台工具分组，且列表头只有标题与工具组", () => {
    const wrapper = mountHeader();

    expect(wrapper.find('button[aria-label="版本历史"]').exists()).toBe(true);
    expect(wrapper.find('button[aria-label="导出为 Markdown"]').exists()).toBe(true);
    expect(wrapper.find('button[aria-label="会话数据分析"]').exists()).toBe(true);
    // 两组之间的分隔线
    expect(wrapper.find('header span[aria-hidden="true"]').exists()).toBe(true);
  });

  it("标题跟随当前会话", () => {
    const wrapper = mountHeader();
    expect(wrapper.find("header h2").text()).toBe("新会话");
  });

  it("显示会话元信息：消息条数 + 最后更新", async () => {
    const wrapper = mountHeader();
    const sessions = useSessionsStore();
    const chat = useChatStore();
    sessions.list = [
      {
        id: "s1",
        title: "知识库中有什么内容？",
        created_at: new Date().toISOString(),
        // 留出余量，避免刚好卡在"3 分钟"边界上
        updated_at: new Date(Date.now() - 3.5 * 60_000).toISOString(),
      },
    ];
    sessions.currentId = "s1";
    chat.messages = [
      { id: "m1", role: "user", content: "问" },
      { id: "m2", role: "assistant", content: "答" },
    ];

    await nextTick();

    expect(wrapper.text()).toContain("2 条消息");
    expect(wrapper.text()).toContain("3 分钟前更新");
  });
});
