import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick, ref } from "vue";
import Sidebar from "@/components/Sidebar.vue";

vi.mock("@/api", () => ({
  searchApi: { search: vi.fn() },
  docsApi: { fileUrl: vi.fn(() => "/file") },
}));

// jsdom 量不到 aside 高度，固定成 900 让上限计算可预期：
// 预算 = 900 - 268(顶部/状态栏预留) - 40*2(两个面板标题栏) - 14(底部留白) = 538
vi.mock("@vueuse/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@vueuse/core")>();
  return { ...actual, useElementSize: () => ({ width: ref(1200), height: ref(900) }) };
});

const DOC_GRIP = '[title^="拖拽调整文档面板高度"]';
const MEM_GRIP = '[title^="拖拽调整记忆面板高度"]';

function mountSidebar(props: Record<string, unknown> = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  return mount(Sidebar, {
    props: {
      healthText: "服务正常",
      healthOk: true,
      width: 236,
      contentWidth: 236,
      open: true,
      ...props,
    },
    global: { plugins: [pinia], stubs: { teleport: true } },
  });
}

function pointer(type: string, clientY: number) {
  return new MouseEvent(type, { clientY, bubbles: true, cancelable: true });
}

/** section 顺序：0=会话区，1=文档，2=记忆；取内容区渲染高度 */
function contentHeight(wrapper: VueWrapper, index: number) {
  const style = wrapper.findAll("section")[index].find("div[style]").attributes("style") ?? "";
  return Number(/(\d+)px/.exec(style)?.[1] ?? NaN);
}

async function drag(wrapper: VueWrapper, selector: string, from: number, to: number) {
  const grip = wrapper.find(selector).element as HTMLElement;
  grip.dispatchEvent(pointer("pointerdown", from));
  window.dispatchEvent(pointer("pointermove", to));
  window.dispatchEvent(pointer("pointerup", to));
  await nextTick();
}

describe("Sidebar 面板高度", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("默认沿用 18vh / 14vh 的内容高度", () => {
    const wrapper = mountSidebar();
    expect(contentHeight(wrapper, 1)).toBe(Math.round(768 * 0.18)); // 138
    expect(contentHeight(wrapper, 2)).toBe(Math.round(768 * 0.14)); // 108
  });

  it("向上拖文档拖拽条：文档变高（不是变矮），记忆高度不受影响", async () => {
    const wrapper = mountSidebar();
    const before = contentHeight(wrapper, 1);

    await drag(wrapper, DOC_GRIP, 500, 400); // 向上 100

    expect(contentHeight(wrapper, 1)).toBe(before + 100);
    expect(contentHeight(wrapper, 2)).toBe(108);
  });

  it("向下拖文档拖拽条：文档变矮，且不小于内容最小高度", async () => {
    const wrapper = mountSidebar();

    await drag(wrapper, DOC_GRIP, 400, 900); // 向下 500

    expect(contentHeight(wrapper, 1)).toBe(137);
  });

  it("向上拖记忆拖拽条：记忆变高，并受「预算减文档占用」限制", async () => {
    const wrapper = mountSidebar();

    await drag(wrapper, MEM_GRIP, 500, 400); // 向上 100
    expect(contentHeight(wrapper, 2)).toBe(208);

    await drag(wrapper, MEM_GRIP, 500, 0); // 一路向上
    expect(contentHeight(wrapper, 2)).toBe(538 - 138);
  });

  it("拖拽结果持久化到单键，双击恢复默认", async () => {
    const wrapper = mountSidebar();

    await drag(wrapper, DOC_GRIP, 500, 450); // 向上 50
    expect(JSON.parse(localStorage.getItem("sidebar-panel-heights") ?? "{}")).toEqual({
      doc: 188,
      mem: 108,
    });

    (wrapper.find(DOC_GRIP).element as HTMLElement).dispatchEvent(
      new MouseEvent("dblclick", { bubbles: true }),
    );
    await nextTick();
    expect(contentHeight(wrapper, 1)).toBe(138);
  });

  it("折叠文档面板只影响自己，记忆高度保持不变", async () => {
    const wrapper = mountSidebar();

    await wrapper.findAll("section")[1].find("button").trigger("click");

    expect(contentHeight(wrapper, 1)).toBe(0);
    expect(contentHeight(wrapper, 2)).toBe(108);
  });

  it("内容区带折叠过渡类，拖拽期间临时关闭以避免滞后", async () => {
    const wrapper = mountSidebar();
    const content = () => wrapper.findAll("section")[1].find("div[style]");
    expect(content().classes()).toContain("sb-panel");

    (wrapper.find(DOC_GRIP).element as HTMLElement).dispatchEvent(pointer("pointerdown", 500));
    await nextTick();
    expect(content().classes()).toContain("sb-panel--dragging");

    window.dispatchEvent(pointer("pointerup", 500));
    await nextTick();
    expect(content().classes()).not.toContain("sb-panel--dragging");
  });

  it("会话列表折叠复用同一套过渡，动画区间取所在区域实测高度", async () => {
    const wrapper = mountSidebar();
    const list = () => wrapper.find(".sb-panel.no-scrollbar");
    expect(list().classes()).toContain("sb-panel");
    // useElementSize 在测试里固定为 900，折叠前用该值作为动画区间（而不是 100vh）
    expect(list().attributes("style")).toContain("max-height: 900px");

    await wrapper.findAll("section")[0].find("button").trigger("click");

    expect(list().attributes("style")).toContain("max-height: 0px");
  });
});

describe("Sidebar 抽屉形态", () => {
  it("窄屏为固定定位悬浮层，且不提供拖拽调宽手柄", () => {
    const wrapper = mountSidebar({ overlay: true });

    expect(wrapper.find("aside").classes()).toContain("fixed");
    expect(wrapper.find('[title="拖拽调整宽度"]').exists()).toBe(false);
  });
});
