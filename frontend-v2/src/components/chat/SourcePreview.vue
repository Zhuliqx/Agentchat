<script setup lang="ts">
import { ref, watch } from "vue";
import Modal from "@/components/common/Modal.vue";
import Icon from "@/components/common/Icon.vue";
import { docsApi } from "@/api";
import { sourceName } from "@/utils/sources";

const props = defineProps<{ source: string }>();
const emit = defineEmits<{ close: [] }>();

const text = ref("");
const binary = ref(false);
const loading = ref(true);
const error = ref("");

/** 只预览前 200k 字符：超大文件截断展示，避免把页面卡住 */
const PREVIEW_LIMIT = 200_000;
const truncated = ref(false);

watch(
  () => props.source,
  async (source) => {
    if (!source) return;
    loading.value = true;
    error.value = "";
    binary.value = false;
    truncated.value = false;
    try {
      const res = await docsApi.preview(source);
      binary.value = res.binary;
      text.value = res.text.slice(0, PREVIEW_LIMIT);
      truncated.value = res.text.length > PREVIEW_LIMIT;
    } catch (e) {
      error.value = (e as Error).message || "读取失败";
    } finally {
      loading.value = false;
    }
  },
  { immediate: true },
);
</script>

<template>
  <Modal :open="true" :title="sourceName(source)" @close="emit('close')">
    <div v-if="loading" class="py-6 text-center text-xs text-ink-dim">加载中…</div>
    <p v-else-if="error" class="text-sm text-err">{{ error }}</p>
    <p v-else-if="binary" class="text-sm text-ink-dim">该文件不是文本，无法预览。</p>
    <template v-else>
      <pre
        class="max-h-[58vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-code-bg p-3 font-mono text-xs leading-relaxed text-code"
        >{{ text }}</pre>
      <p v-if="truncated" class="mt-2 text-2xs text-ink-faint">
        文件较大，仅预览前 {{ PREVIEW_LIMIT.toLocaleString() }} 个字符。
      </p>
    </template>

    <div class="mt-3 flex items-center gap-2 border-t border-line pt-3">
      <span class="min-w-0 flex-1 truncate text-2xs text-ink-faint" :title="source">{{
        source
      }}</span>
      <a
        :href="docsApi.fileUrl(source, true)"
        class="flex h-7 flex-shrink-0 items-center gap-1 rounded-md border border-line-2 px-2.5 text-2xs text-ink-dim transition hover:border-accent/50 hover:text-ink"
      >
        <Icon name="download" :size="12" />
        下载原文件
      </a>
    </div>
  </Modal>
</template>
