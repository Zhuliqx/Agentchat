import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import AppDialog from "@/components/common/AppDialog.vue";
import { useDialogStore } from "@/stores/dialog";

function mountDialog() {
  return mount(AppDialog, { global: { stubs: { teleport: true } } });
}

describe("AppDialog", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders a confirm and resolves true on 确定", async () => {
    const dialog = useDialogStore();
    const answer = dialog.confirm("确认删除？");
    const wrapper = mountDialog();
    expect(wrapper.text()).toContain("确认删除？");

    await wrapper.get('[data-testid="dialog-confirm"]').trigger("click");

    await expect(answer).resolves.toBe(true);
    wrapper.unmount();
  });

  it("returns the typed value for prompt", async () => {
    const dialog = useDialogStore();
    const answer = dialog.prompt("设置标签", "旧值");
    const wrapper = mountDialog();
    const input = wrapper.get<HTMLInputElement>('[data-testid="dialog-input"]');
    await input.setValue("新值");

    await wrapper.get('[data-testid="dialog-confirm"]').trigger("click");

    await expect(answer).resolves.toBe("新值");
    wrapper.unmount();
  });
});
