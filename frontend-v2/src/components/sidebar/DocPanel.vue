<script setup lang="ts">
import { ref } from "vue";
import { useDocsStore } from "@/stores/docs";
import EmptyState from "@/components/common/EmptyState.vue";
import Icon from "@/components/common/Icon.vue";

const docs = useDocsStore();
const preview = ref<{ title: string; open: boolean; text: string; binary: boolean; source: string }>({
  title: "",
  open: false,
  text: "",
  binary: false,
  source: "",
});

const inputRef = ref<HTMLInputElement | null>(null);

async function onFiles(e: Event) {
  const files = (e.target as HTMLInputElement).files;
  if (!files?.length) return;
  const r = await docs.upload(Array.from(files));
  alert(`摄入完成：${r.filename} → ${r.chunks} 个分块`);
}

async function showDoc(source: string, filename: string) {
  try {
    const p = await docsApiPreview(source);
    preview.value = { title: filename, open: true, text: p.text, binary: p.binary, source };
  } catch (err) {
    alert(String((err as Error).message));
  }
}

// 简单内联：预览用 docsApi（避免循环导入）
import { docsApi } from "@/api";
async function docsApiPreview(source: string) {
  return docsApi.preview(source);
}

function openUrl(u: string) {
  window.open(u, "_blank");
}
function copyText(t: string) {
  navigator.clipboard?.writeText(t).catch(() => {});
}

async function remove(source: string) {
  if (!confirm("确定从知识库删除该文档？")) return;
  await docs.remove(source);
}
</script>

<template>
  <div>
    <div
      class="group mb-1.5 flex cursor-pointer flex-col items-center gap-1 rounded-lg border border-dashed border-line-2 px-2 py-3 text-center transition hover:border-accent/50 hover:bg-accent/4"
      @click="inputRef?.click()"
      @dragover.prevent
      @drop.prevent="(e: DragEvent) => { if (e.dataTransfer?.files?.length) docs.upload(Array.from(e.dataTransfer.files)); }"
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
      <span class="text-[11px] text-ink-dim">点击或拖入文件</span>
      <span class="text-[10px] text-ink-faint">txt · md · pdf · docx · html</span>
    </div>
    <div v-if="docs.list.length" class="flex flex-col gap-px">
      <div
        v-for="d in docs.list"
        :key="d.id"
        class="group flex items-center gap-2 rounded-md px-1.5 py-[5px] text-[12px] text-ink-dim transition hover:bg-surface-2 hover:text-ink"
      >
        <Icon :name="d.has_file ? 'doc' : 'folder'" :size="13" class="flex-shrink-0 text-ink-faint" />
        <span
          class="min-w-0 flex-1 cursor-pointer truncate"
          :title="d.source"
          @click="showDoc(d.source, d.filename)"
        >
          {{ d.filename }}
        </span>
        <span class="flex-shrink-0 text-[10px] text-ink-faint">{{ d.chunks }}</span>
        <button
          class="hidden flex-shrink-0 text-ink-faint transition hover:text-err group-hover:block"
          title="删除文档"
          @click="remove(d.source)"
        >
          <Icon name="trash" :size="12" />
        </button>
      </div>
    </div>
    <EmptyState v-else text="暂无文档" />

    <!-- 文档预览弹窗 -->
    <Teleport to="body">
      <Transition name="fade">
        <div
          v-if="preview.open"
          class="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-[2px]"
          @click.self="preview.open = false"
        >
          <div class="flex max-h-[80vh] w-[560px] max-w-[92vw] flex-col overflow-hidden rounded-xl border border-line-2 bg-surface shadow-[0_24px_64px_rgba(0,0,0,0.5)]">
            <div class="flex flex-shrink-0 items-center justify-between border-b border-line px-5 py-3">
              <span class="truncate text-[13.5px] font-medium">{{ preview.title }}</span>
              <button
                class="grid h-7 w-7 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
                aria-label="关闭"
                @click="preview.open = false"
              >
                <Icon name="x" :size="15" />
              </button>
            </div>
            <div class="min-h-0 flex-1 overflow-auto px-5 py-4">
              <div class="mb-3 flex gap-2">
                <button
                  class="flex items-center gap-1.5 rounded-md border border-line-2 px-2.5 py-1.5 text-[11.5px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
                  @click="openUrl(docsApi.fileUrl(preview.source, true))"
                >
                  <Icon name="download" :size="12" />
                  下载原始文件
                </button>
                <button
                  class="flex items-center gap-1.5 rounded-md border border-line-2 px-2.5 py-1.5 text-[11.5px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
                  @click="copyText(preview.source)"
                >
                  <Icon name="copy" :size="12" />
                  复制路径
                </button>
              </div>
              <pre
                v-if="preview.binary"
                class="text-[12px] text-ink-faint"
              >该文件为二进制格式，请下载查看。</pre>
              <pre
                v-else
                class="whitespace-pre-wrap break-all rounded-lg border border-line bg-code-bg p-3.5 text-[12px] leading-relaxed text-ink-dim"
              >{{ preview.text.length > 3000 ? preview.text.slice(0, 3000) + "\n…（已截断，可下载完整文件）" : preview.text }}</pre>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>
