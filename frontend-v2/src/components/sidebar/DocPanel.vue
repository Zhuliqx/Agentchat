<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from "vue";
import { ApiError, docsApi } from "@/api";
import { useDocsStore } from "@/stores/docs";
import { useDialogStore } from "@/stores/dialog";
import EmptyState from "@/components/common/EmptyState.vue";
import Icon from "@/components/common/Icon.vue";
import Modal from "@/components/common/Modal.vue";
import Skeleton from "@/components/common/Skeleton.vue";

interface UploadItem {
  taskId: string;
  filename: string;
  progress: number;
  stage: string;
  status: "pending" | "processing" | "done" | "error";
}

const docs = useDocsStore();
const ui = useDialogStore();
const preview = ref<{
  title: string;
  open: boolean;
  text: string;
  binary: boolean;
  source: string;
}>({
  title: "",
  open: false,
  text: "",
  binary: false,
  source: "",
});
const inputRef = ref<HTMLInputElement | null>(null);

// ---- 多选 / 批量操作 ----
const selecting = ref(false);
const selected = ref<Set<string>>(new Set());

function toggleSelectMode() {
  selecting.value = !selecting.value;
  selected.value = new Set();
}
function toggleSel(source: string) {
  const s = new Set(selected.value);
  if (s.has(source)) s.delete(source);
  else s.add(source);
  selected.value = s;
}
function toggleAll() {
  selected.value =
    selected.value.size === docs.list.length ? new Set() : new Set(docs.list.map((d) => d.source));
}
async function removeSelected() {
  if (!selected.value.size) return;
  if (!(await ui.confirm(`确定从知识库删除选中的 ${selected.value.size} 个文档？`))) return;
  try {
    await docs.removeMany(Array.from(selected.value));
    selected.value = new Set();
  } catch (e) {
    await ui.alertError("批量删除失败", e);
  }
}

async function onFiles(e: Event) {
  const files = (e.target as HTMLInputElement).files;
  if (!files?.length) return;
  try {
    await startUpload(Array.from(files));
  } catch (err) {
    await ui.alertError("上传失败", err);
  }
}

// ---- 上传进度（后台任务轮询） ----
const uploading = ref<UploadItem[]>([]);
const pollControllers = new Set<AbortController>();
let activeUploads = 0;
let disposed = false;

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onAbort = () => {
      if (timer) clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function pollIngest(item: UploadItem, signal: AbortSignal) {
  // 轮询直到任务结束；瞬时失败指数退避（上限 5s），404/410 直接判过期
  let delay = 800;
  let failures = 0;
  for (;;) {
    try {
      await sleep(delay, signal);
    } catch {
      return; // 组件卸载/主动取消
    }
    let st;
    try {
      st = await docsApi.ingestStatus(item.taskId, signal);
      failures = 0;
      delay = 800;
    } catch (err) {
      if (signal.aborted) return;
      if (err instanceof ApiError && (err.status === 404 || err.status === 410)) {
        item.status = "error";
        item.stage = "任务已过期";
        break;
      }
      failures += 1;
      if (failures >= 5) {
        item.status = "error";
        item.stage = "状态查询失败";
        break;
      }
      delay = Math.min(delay * 2, 5000);
      continue;
    }
    item.progress = st.progress;
    item.stage = st.stage;
    if (st.status === "done") {
      item.status = "done";
      item.progress = 100;
      item.stage = "完成";
      break;
    }
    if (st.status === "error") {
      item.status = "error";
      item.stage = st.error || "失败";
      break;
    }
  }
}

async function onDrop(e: DragEvent) {
  const files = e.dataTransfer?.files;
  if (!files?.length) return;
  try {
    await startUpload(Array.from(files));
  } catch (err) {
    await ui.alertError("上传失败", err);
  }
}

async function trackIngest(item: UploadItem) {
  const controller = new AbortController();
  pollControllers.add(controller);
  activeUploads += 1;
  try {
    await pollIngest(item, controller.signal);
  } finally {
    pollControllers.delete(controller);
    activeUploads -= 1;
    if (!disposed) {
      uploading.value = uploading.value.filter((u) => u.taskId !== item.taskId);
      if (activeUploads === 0) await docs.load(); // 多文件完成只刷新一次
    }
  }
}

onBeforeUnmount(() => {
  disposed = true;
  pollControllers.forEach((c) => c.abort());
  pollControllers.clear();
});

async function startUpload(files: File[]) {
  const tasks = await docs.upload(Array.from(files));
  for (const t of tasks) {
    const item = reactive<UploadItem>({
      taskId: t.task_id,
      filename: t.filename,
      progress: 0,
      stage: "排队中",
      status: "pending",
    });
    uploading.value.push(item);
    void trackIngest(item);
  }
}

async function showDoc(source: string, filename: string) {
  try {
    const p = await docsApi.preview(source);
    preview.value = { title: filename, open: true, text: p.text, binary: p.binary, source };
  } catch (err) {
    await ui.alertError("打开文档失败", err);
  }
}

function openUrl(u: string) {
  window.open(u, "_blank");
}
function copyText(t: string) {
  navigator.clipboard?.writeText(t).catch(() => {});
}

async function remove(source: string) {
  if (!(await ui.confirm("确定从知识库删除该文档？"))) return;
  try {
    await docs.remove(source);
  } catch (e) {
    await ui.alertError("删除文档失败", e);
  }
}

async function editTag(d: { source: string; tag?: string | null }) {
  const input = await ui.prompt("设置文档标签（留空或取消可清除）：", d.tag || "");
  if (input === null) return; // 取消
  try {
    await docs.setTag(d.source, input.trim() || null);
  } catch (e) {
    await ui.alertError("设置标签失败", e);
  }
}
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="flex-none">
      <div
        class="group mb-1.5 flex cursor-pointer flex-col items-center gap-1 rounded-lg border border-dashed border-line-2 px-2 py-3 text-center transition hover:border-accent/50 hover:bg-accent/4"
        @click="inputRef?.click()"
        @dragover.prevent
        @drop.prevent="onDrop"
      >
        <input
          ref="inputRef"
          type="file"
          multiple
          class="hidden"
          accept=".txt,.md,.pdf,.docx,.html"
          @change="onFiles"
        />
        <Icon name="upload" :size="15" class="text-ink-faint transition group-hover:text-accent" />
        <span class="text-2xs text-ink-dim">点击或拖入文件</span>
        <span class="text-2xs text-ink-faint">txt · md · pdf · docx · html</span>
      </div>
      <!-- 上传进度 -->
      <div v-if="uploading.length" class="mb-1.5 flex flex-col gap-1.5">
        <div
          v-for="u in uploading"
          :key="u.taskId"
          class="rounded-md border border-line bg-surface-2 px-2.5 py-2"
        >
          <div class="flex items-center justify-between gap-2 text-2xs">
            <span class="min-w-0 truncate text-ink-dim">{{ u.filename }}</span>
            <span
              class="flex-shrink-0"
              :class="u.status === 'error' ? 'text-err' : 'text-ink-faint'"
            >
              {{ u.stage }}
            </span>
          </div>
          <div class="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-3">
            <div
              class="h-full rounded-full transition-all duration-300"
              :class="u.status === 'error' ? 'bg-err' : 'bg-accent'"
              :style="{ width: u.progress + '%' }"
            />
          </div>
        </div>
      </div>
    </div>
    <div v-if="docs.list.length" class="no-scrollbar min-h-0 flex-1 overflow-y-auto">
      <div class="flex flex-col gap-px">
        <div class="mb-0.5 flex items-center justify-between px-1">
          <button
            class="text-2xs text-ink-faint transition hover:text-accent coarse:grid coarse:h-8 coarse:min-w-8 coarse:place-items-center"
            @click="toggleSelectMode"
          >
            {{ selecting ? "完成" : "多选" }}
          </button>
          <span v-if="selecting" class="text-2xs text-ink-faint">{{ selected.size }} 已选</span>
        </div>
        <div
          v-for="d in docs.list"
          :key="d.id"
          class="group flex items-center gap-2 rounded-md px-1.5 py-[5px] text-xs text-ink-dim transition hover:bg-surface-2 hover:text-ink"
          :class="selecting && selected.has(d.source) ? 'bg-accent/8' : ''"
        >
          <input
            v-if="selecting"
            type="checkbox"
            class="h-3.5 w-3.5 flex-shrink-0 accent-[var(--color-accent)]"
            :checked="selected.has(d.source)"
            @change="toggleSel(d.source)"
          />
          <Icon
            :name="d.has_file ? 'doc' : 'folder'"
            :size="13"
            class="flex-shrink-0 text-ink-faint"
          />
          <span
            class="min-w-0 flex-1 cursor-pointer truncate"
            :title="d.source"
            @click="selecting ? toggleSel(d.source) : showDoc(d.source, d.filename)"
          >
            {{ d.filename }}
          </span>
          <span class="flex-shrink-0 text-2xs text-ink-faint">{{ d.chunks }}</span>
          <button
            v-if="!selecting"
            class="flex-shrink-0 rounded-full px-1.5 py-px text-2xs transition coarse:min-h-8 coarse:px-2.5 coarse:py-1.5"
            :class="
              d.tag
                ? 'border border-accent/40 text-accent hover:border-accent'
                : 'border border-dashed border-line-2 text-ink-faint hover:border-accent/50 hover:text-accent'
            "
            :title="d.tag ? '点击编辑标签' : '添加标签'"
            @click.stop="editTag(d)"
          >
            {{ d.tag || "+ 标签" }}
          </button>
          <button
            v-if="!selecting"
            class="hidden flex-shrink-0 text-ink-faint transition hover:text-err group-hover:block"
            title="删除文档"
            @click="remove(d.source)"
          >
            <Icon name="trash" :size="12" />
          </button>
        </div>
        <!-- 多选操作栏 -->
        <div v-if="selecting" class="mt-1 flex items-center gap-1.5 px-1">
          <button class="text-2xs text-ink-dim transition hover:text-ink" @click="toggleAll">
            {{ selected.size === docs.list.length ? "取消全选" : "全选" }}
          </button>
          <span class="mx-0.5 h-3 w-px bg-line" />
          <button
            class="text-2xs text-err transition hover:brightness-125 disabled:cursor-not-allowed disabled:opacity-40"
            :disabled="!selected.size"
            @click="removeSelected"
          >
            删除（{{ selected.size }}）
          </button>
        </div>
      </div>
    </div>
    <div v-else-if="docs.loading" class="px-0.5 pt-0.5">
      <Skeleton :rows="3" />
      <span class="sr-only">正在加载文档…</span>
    </div>
    <EmptyState v-else text="暂无文档" />

    <!-- 文档预览弹窗 -->
    <Modal title="文档预览" :open="preview.open" @close="preview.open = false">
      <div class="mb-3 flex gap-2">
        <button
          class="flex items-center gap-1.5 rounded-md border border-line-2 px-2.5 py-1.5 text-2xs text-ink-dim transition hover:border-accent/50 hover:text-ink"
          @click="openUrl(docsApi.fileUrl(preview.source, true))"
        >
          <Icon name="download" :size="12" />
          下载原始文件
        </button>
        <button
          class="flex items-center gap-1.5 rounded-md border border-line-2 px-2.5 py-1.5 text-2xs text-ink-dim transition hover:border-accent/50 hover:text-ink"
          @click="copyText(preview.source)"
        >
          <Icon name="copy" :size="12" />
          复制路径
        </button>
      </div>
      <pre v-if="preview.binary" class="text-xs text-ink-faint">
该文件为二进制格式，请下载查看。</pre>
      <pre
        v-else
        class="whitespace-pre-wrap break-all rounded-lg border border-line bg-code-bg p-3.5 text-xs leading-relaxed text-ink-dim"
        >{{
          preview.text.length > 3000
            ? preview.text.slice(0, 3000) + "\n…（已截断，可下载完整文件）"
            : preview.text
        }}</pre>
    </Modal>
  </div>
</template>
