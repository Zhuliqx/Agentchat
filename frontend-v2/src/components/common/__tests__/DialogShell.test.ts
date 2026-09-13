import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import DialogShell from "@/components/common/DialogShell.vue";

describe("DialogShell", () => {
  it("加载中渲染骨架屏，并给读屏保留文案", () => {
    const wrapper = mount(DialogShell, {
      props: { loading: true, loadingRows: 3 },
      slots: { default: "<p>内容</p>" },
    });

    expect(wrapper.findAll(".animate-pulse")).toHaveLength(3);
    expect(wrapper.text()).toContain("加载中…");
    expect(wrapper.text()).not.toContain("内容");
  });

  it("出错时只展示错误信息", () => {
    const wrapper = mount(DialogShell, {
      props: { error: "请求失败" },
      slots: { default: "<p>内容</p>" },
    });

    expect(wrapper.text()).toContain("请求失败");
    expect(wrapper.text()).not.toContain("内容");
  });

  it("默认渲染插槽内容", () => {
    const wrapper = mount(DialogShell, { slots: { default: "<p>内容</p>" } });
    expect(wrapper.text()).toContain("内容");
  });
});
