import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useDialogStore } from "@/stores/dialog";

describe("dialog store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("resolves confirm with the chosen boolean and clears state", async () => {
    const dialog = useDialogStore();
    const confirmed = dialog.confirm("删除？");
    expect(dialog.current?.kind).toBe("confirm");

    dialog.resolve(true);

    await expect(confirmed).resolves.toBe(true);
    expect(dialog.current).toBeNull();
  });

  it("resolves prompt with the submitted value or null", async () => {
    const dialog = useDialogStore();
    const submitted = dialog.prompt("标签", "旧值");
    dialog.resolve("新值");
    await expect(submitted).resolves.toBe("新值");

    const cancelled = dialog.prompt("标签");
    dialog.resolve(null);
    await expect(cancelled).resolves.toBeNull();
  });

  it("resolves alert without a value", async () => {
    const dialog = useDialogStore();
    const acknowledged = dialog.alert("下载失败");
    dialog.resolve(undefined);
    await expect(acknowledged).resolves.toBeUndefined();
  });
});
