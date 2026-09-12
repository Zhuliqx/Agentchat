import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import Dropdown from "@/components/common/Dropdown.vue";

function mountDropdown(open: boolean) {
  return mount(Dropdown, {
    props: { open },
    slots: {
      trigger: '<button type="button">打开</button>',
      default: '<button type="button">选项</button>',
    },
  });
}

describe("Dropdown keyboard", () => {
  it("closes on Escape from the trigger", async () => {
    const wrapper = mountDropdown(true);
    await wrapper.find('button[type="button"]').trigger("keydown", {
      key: "Escape",
    });
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("does not emit close when already closed", async () => {
    const wrapper = mountDropdown(false);
    await wrapper.find('button[type="button"]').trigger("keydown", {
      key: "Escape",
    });
    expect(wrapper.emitted("close")).toBeUndefined();
    wrapper.unmount();
  });
});
