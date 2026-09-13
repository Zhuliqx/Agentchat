import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import Tooltip from "@/components/common/Tooltip.vue";

function mountTooltip() {
  return mount(Tooltip, {
    props: { label: "会话数据分析" },
    slots: { default: '<button aria-label="会话数据分析">x</button>' },
    attachTo: document.body,
    global: { stubs: { teleport: true } },
  });
}

describe("Tooltip", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("悬停后延迟显示，移开即隐藏", async () => {
    const wrapper = mountTooltip();
    // Tooltip 是「触发器 + 传送门」多根组件，事件需派发到触发器本身
    const trigger = wrapper.find("span.inline-flex");
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false);

    await trigger.trigger("mouseenter");
    vi.advanceTimersByTime(299);
    await flushPromises();
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false);

    vi.advanceTimersByTime(2);
    await flushPromises();
    expect(wrapper.find('[role="tooltip"]').text()).toBe("会话数据分析");

    await trigger.trigger("mouseleave");
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false);
  });

  it("键盘聚焦同样可见，并在提示可见时关联 aria-describedby", async () => {
    const wrapper = mountTooltip();
    const trigger = wrapper.find("span.inline-flex");
    await trigger.trigger("focusin");
    vi.advanceTimersByTime(300);
    await flushPromises();

    const describedBy = trigger.attributes("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(wrapper.find('[role="tooltip"]').attributes("id")).toBe(describedBy);

    await trigger.trigger("keydown.escape");
    expect(wrapper.find('[role="tooltip"]').exists()).toBe(false);
  });

  it("调用方传入的 class 透传到触发器上", () => {
    const wrapper = mount(Tooltip, {
      props: { label: "复制" },
      attrs: { class: "ml-auto" },
      slots: { default: "<button>x</button>" },
      global: { stubs: { teleport: true } },
    });

    expect(wrapper.find("span.inline-flex").classes()).toContain("ml-auto");
  });

  it("上方放不下时翻面到下方，不跑出视口", async () => {
    const wrapper = mountTooltip();
    const trigger = wrapper.find("span.inline-flex");

    // jsdom 里元素矩形都是 0，触发"默认方向放不下"的分支
    await trigger.trigger("focusin");
    vi.advanceTimersByTime(300);
    await flushPromises();

    const tooltip = wrapper.find('[role="tooltip"]');
    expect(tooltip.exists()).toBe(true);
    expect(tooltip.attributes("style")).toContain("translate(-50%, 0)");
  });
});
