import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import Sidebar from "@/components/Sidebar.vue";

vi.mock("@/api", () => ({
  searchApi: { search: vi.fn() },
  docsApi: { fileUrl: vi.fn(() => "/file") },
}));

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

function pointer(type: string, clientX: number) {
  return new MouseEvent(type, { clientX, bubbles: true, cancelable: true });
}

describe("Sidebar 折叠交互", () => {
  it("拖拽收窄到阈值以下触发折叠，折叠中继续左移不再改宽度，反向拖回可展开", () => {
    const wrapper = mountSidebar();
    const handle = wrapper.find('[title="拖拽调整宽度"]').element as HTMLElement;

    // 起点 300：raw = 236 + clientX - 300
    handle.dispatchEvent(pointer("pointerdown", 300));
    window.dispatchEvent(pointer("pointermove", 264)); // raw=200，仍在阈值以上
    expect(wrapper.emitted("width-change")).toHaveLength(1);
    expect(wrapper.emitted("toggle")).toBeUndefined();

    window.dispatchEvent(pointer("pointermove", 164)); // raw=100，低于阈值 → 折叠
    expect(wrapper.emitted("toggle")).toHaveLength(1);

    window.dispatchEvent(pointer("pointermove", 100)); // 折叠中继续左移：不应再发宽度
    expect(wrapper.emitted("width-change")).toHaveLength(1);

    window.dispatchEvent(pointer("pointermove", 254)); // raw=190，拖回阈值以上 → 展开
    expect(wrapper.emitted("toggle")).toHaveLength(2);
    expect(wrapper.emitted("width-change")).toHaveLength(2);

    window.dispatchEvent(pointer("pointerup", 254));
  });

  it("折叠态下内容层保持展开宽度，且保留把手可从左边缘拖开", () => {
    const wrapper = mountSidebar({ width: 0, contentWidth: 236, open: false });

    expect((wrapper.find(".sb-inner").element as HTMLElement).style.width).toBe("236px");
    const handle = wrapper.find('[title="拖拽调整宽度"]').element as HTMLElement;
    expect(handle).toBeTruthy();
    // 折叠态从最小宽度起步：往右拖 20px → 直接展开到 200px
    handle.dispatchEvent(pointer("pointerdown", 200));
    window.dispatchEvent(pointer("pointermove", 220));
    expect(wrapper.emitted("toggle")).toHaveLength(1);
    expect(wrapper.emitted("width-change")?.at(-1)).toEqual([200]);
    window.dispatchEvent(pointer("pointerup", 220));
  });
});
