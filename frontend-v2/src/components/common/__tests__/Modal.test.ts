import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import Modal from "@/components/common/Modal.vue";

function mountModal(open = true) {
  return mount(Modal, {
    props: { title: "设置", open },
    attachTo: document.body,
    global: { stubs: { teleport: true } },
  });
}

describe("Modal accessibility", () => {
  beforeEach(() => {
    document.body.style.overflow = "";
  });

  it("exposes dialog semantics and labels the title", () => {
    const wrapper = mountModal();
    const dialog = wrapper.find('[role="dialog"]');
    expect(dialog.exists()).toBe(true);
    expect(dialog.attributes("aria-modal")).toBe("true");
    const labelledBy = dialog.attributes("aria-labelledby");
    expect(labelledBy).toBeTruthy();
    expect(wrapper.find(`#${labelledBy}`).text()).toContain("设置");
    wrapper.unmount();
  });

  it("closes on Escape", async () => {
    const wrapper = mountModal();
    await wrapper.find('[role="dialog"]').trigger("keydown", { key: "Escape" });
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("locks body scroll while open and restores it on close", async () => {
    const wrapper = mountModal(false);
    await wrapper.setProps({ open: true });
    expect(document.body.style.overflow).toBe("hidden");

    await wrapper.setProps({ open: false });
    expect(document.body.style.overflow).toBe("");
    wrapper.unmount();
  });

  it("moves focus into the dialog when opened", async () => {
    const wrapper = mountModal(false);
    await wrapper.setProps({ open: true });
    await nextTick();
    const dialog = wrapper.find('[role="dialog"]').element;
    expect(dialog.contains(document.activeElement)).toBe(true);
    wrapper.unmount();
  });
});
