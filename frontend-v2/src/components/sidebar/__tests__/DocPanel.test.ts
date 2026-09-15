import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import DocPanel from "@/components/sidebar/DocPanel.vue";

vi.mock("@/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    docsApi: {
      list: vi.fn(async () => []),
      upload: vi.fn(),
      ingestStatus: vi.fn(),
      remove: vi.fn(),
      batchRemove: vi.fn(),
      setTag: vi.fn(),
      preview: vi.fn(),
      fileUrl: vi.fn(() => "/file"),
    },
  };
});

import { docsApi } from "@/api";
import { useDialogStore } from "@/stores/dialog";
import { useDocsStore } from "@/stores/docs";

const uploadApi = docsApi.upload as unknown as ReturnType<typeof vi.fn>;
const ingestStatusApi = docsApi.ingestStatus as unknown as ReturnType<typeof vi.fn>;
const listApi = docsApi.list as unknown as ReturnType<typeof vi.fn>;

async function selectFile(wrapper: VueWrapper) {
  const input = wrapper.find('input[type="file"]');
  Object.defineProperty(input.element, "files", {
    value: [new File(["x"], "a.txt")],
    configurable: true,
  });
  await input.trigger("change");
  await flushPromises();
}

describe("DocPanel ingest polling", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("stops polling after unmount", async () => {
    uploadApi.mockResolvedValue({
      tasks: [{ task_id: "t1", filename: "a.txt", file_path: "/tmp/a" }],
    });
    ingestStatusApi.mockResolvedValue({
      status: "processing",
      progress: 10,
      stage: "转换中",
      filename: "a.txt",
    });
    const wrapper = mount(DocPanel);
    await selectFile(wrapper);
    await vi.advanceTimersByTimeAsync(800);
    expect(ingestStatusApi).toHaveBeenCalledTimes(1);

    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(6000);

    expect(ingestStatusApi).toHaveBeenCalledTimes(1);
  });

  it("refreshes the document list once when all uploads finish", async () => {
    uploadApi.mockResolvedValue({
      tasks: [
        { task_id: "t1", filename: "a.txt", file_path: "/tmp/a" },
        { task_id: "t2", filename: "b.txt", file_path: "/tmp/b" },
      ],
    });
    ingestStatusApi.mockResolvedValue({
      status: "done",
      progress: 100,
      stage: "完成",
      filename: "a.txt",
    });
    const wrapper = mount(DocPanel);
    await selectFile(wrapper);

    await vi.advanceTimersByTimeAsync(800);
    await flushPromises();

    expect(ingestStatusApi).toHaveBeenCalledTimes(2);
    expect(listApi).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("retries transient status failures with backoff", async () => {
    uploadApi.mockResolvedValue({
      tasks: [{ task_id: "t1", filename: "a.txt", file_path: "/tmp/a" }],
    });
    ingestStatusApi
      .mockRejectedValueOnce(new Error("network"))
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({
        status: "done",
        progress: 100,
        stage: "完成",
        filename: "a.txt",
      });
    const wrapper = mount(DocPanel);
    await selectFile(wrapper);

    await vi.advanceTimersByTimeAsync(800); // 第一次失败
    await vi.advanceTimersByTimeAsync(1600); // 退避后第二次失败
    await vi.advanceTimersByTimeAsync(3200); // 再次退避后成功
    await flushPromises();

    expect(ingestStatusApi).toHaveBeenCalledTimes(3);
    expect(listApi).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("删除文档失败时弹出统一错误提示", async () => {
    const removeApi = docsApi.remove as unknown as ReturnType<typeof vi.fn>;
    removeApi.mockRejectedValueOnce(new Error("后端不可达"));
    const docs = useDocsStore();
    docs.list = [
      {
        id: "d1",
        filename: "a.md",
        source: "data/kb/a.md",
        chunks: 1,
        has_file: true,
      },
    ];
    const wrapper = mount(DocPanel);
    const ui = useDialogStore();

    await wrapper.find('button[title="删除文档"]').trigger("click");
    ui.resolve(true); // 确认删除
    await flushPromises();

    expect(ui.current?.kind).toBe("alert");
    expect(ui.current?.message).toContain("删除文档失败");
    expect(ui.current?.message).toContain("后端不可达");
    ui.resolve(undefined);
    wrapper.unmount();
  });
});
