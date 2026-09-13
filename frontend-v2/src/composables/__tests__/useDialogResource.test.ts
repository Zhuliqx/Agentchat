import { describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";
import { flushPromises } from "@vue/test-utils";
import { useDialogResource } from "@/composables/useDialogResource";

function deferred() {
  let resolve!: () => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useDialogResource", () => {
  it("挂载时 open 已为 true（v-if 弹窗）也会立即取数", async () => {
    const open = ref(true);
    const fetcher = vi.fn(async () => {});

    const { loading } = useDialogResource(open, fetcher);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(loading.value).toBe(true);
    await flushPromises();
    expect(loading.value).toBe(false);
  });

  it("关闭状态挂载时不取数，打开后取数并执行 onOpen", async () => {
    const open = ref(false);
    const fetcher = vi.fn(async () => {});
    const onOpen = vi.fn();

    useDialogResource(open, fetcher, { onOpen });
    expect(fetcher).not.toHaveBeenCalled();

    open.value = true;
    await nextTick();
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("打开期间依赖 key 变化时重新取数，但不重复执行 onOpen", async () => {
    const open = ref(true);
    const key = ref("s1");
    const fetcher = vi.fn(async () => {});
    const onOpen = vi.fn();

    useDialogResource(open, fetcher, { key: () => key.value, onOpen });
    expect(onOpen).toHaveBeenCalledTimes(1);

    key.value = "s2";
    await nextTick();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("关闭期间依赖 key 变化不取数", async () => {
    const open = ref(false);
    const key = ref("s1");
    const fetcher = vi.fn(async () => {});

    useDialogResource(open, fetcher, { key: () => key.value });
    key.value = "s2";
    await nextTick();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("过期请求不覆盖最新状态", async () => {
    const open = ref(true);
    const key = ref("s1");
    const requests: ReturnType<typeof deferred>[] = [];
    const fetcher = vi.fn(() => {
      const d = deferred();
      requests.push(d);
      return d.promise;
    });

    const { loading, error } = useDialogResource(open, fetcher, { key: () => key.value });
    key.value = "s2";
    await nextTick();
    expect(requests).toHaveLength(2);

    requests[1].reject(new Error("最新请求失败"));
    await flushPromises();
    expect(error.value).toBe("最新请求失败");

    // 旧请求随后才返回：既不能清掉错误，也不能改写 loading
    requests[0].resolve();
    await flushPromises();
    expect(error.value).toBe("最新请求失败");
    expect(loading.value).toBe(false);
  });

  it("取数失败时记录错误并结束 loading，reload 可重试", async () => {
    const open = ref(true);
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(undefined);

    const { loading, error, reload } = useDialogResource(open, fetcher);
    await flushPromises();
    expect(error.value).toBe("boom");
    expect(loading.value).toBe(false);

    await reload();
    expect(error.value).toBe("");
    expect(loading.value).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
