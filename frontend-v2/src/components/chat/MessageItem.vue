<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useThrottleFn } from "@vueuse/core";
import { useChatStore, type ChatMsg } from "@/stores/chat";
import { md } from "@/utils/markdown";
import {
  decorateCitations,
  decorateMarkdown,
  handleCodeCopyClick,
  highlightSourceChip,
  syncTypingCaret,
} from "@/utils/markdownEnhance";
import { useAuthStore } from "@/stores/auth";
import { docsApi } from "@/api";
import OrbitFlow from "./OrbitFlow.vue";
import SourcePreview from "./SourcePreview.vue";
import Icon from "@/components/common/Icon.vue";
import Tooltip from "@/components/common/Tooltip.vue";
import { avatarColor } from "@/utils/avatar";
import { sourceName, sourceTitle } from "@/utils/sources";

const props = defineProps<{ msg: ChatMsg }>();
const chat = useChatStore();
const auth = useAuthStore();

const userInitial = computed(() => (auth.user?.username || "我").slice(0, 1).toUpperCase());

/** 消息时间只显示时分，例如 14:32 */
function timeLabel(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const html = ref("");
const bodyRef = ref<HTMLElement | null>(null);
const render = useThrottleFn(
  () => {
    html.value = props.msg.streaming
      ? md.renderStream(props.msg.content)
      : md.render(props.msg.content);
  },
  60,
  true,
);
watch(() => [props.msg.content, props.msg.streaming], render, { immediate: true });
// v-html 重建 DOM 后补上代码块复制按钮与表格滚动容器。
// 首次渲染时 html 已在 setup 期间赋值（watch 注册晚于赋值），所以用 onMounted 兜底。
onMounted(() => {
  decorateMarkdown(bodyRef.value);
  decorateCitations(bodyRef.value, props.msg.sources?.length ?? 0);
  syncTypingCaret(bodyRef.value, !!props.msg.streaming);
});
watch(
  html,
  () => {
    decorateMarkdown(bodyRef.value);
    decorateCitations(bodyRef.value, props.msg.sources?.length ?? 0);
    syncTypingCaret(bodyRef.value, !!props.msg.streaming);
  },
  { flush: "post" },
);
// 来源列表变化（流式补发来源）后重新标注引用编号
watch(
  () => props.msg.sources?.length ?? 0,
  (count) => decorateCitations(bodyRef.value, count),
  { flush: "post" },
);
// 流式结束：移除光标（它现在在最后一段内部，不移除会留在正文里）
watch(
  () => props.msg.streaming,
  (now) => syncTypingCaret(bodyRef.value, !!now),
  { flush: "post" },
);

async function onMarkdownClick(e: MouseEvent) {
  if (await handleCodeCopyClick(e.target)) return;
  const target = e.target as HTMLElement | null;
  const cite = target?.closest<HTMLElement>("[data-cite]");
  if (cite?.dataset.cite) highlightSourceChip(bodyRef.value, Number(cite.dataset.cite));
}

/** 用户气泡里的 Markdown 同样补增强（可能粘贴代码块） */
const userBodyRef = ref<HTMLElement | null>(null);
onMounted(() => decorateMarkdown(userBodyRef.value));
watch(
  () => props.msg.content,
  () => decorateMarkdown(userBodyRef.value),
  { flush: "post" },
);

const copied = ref(false);
// 引用溯源：点击来源在应用内预览（原始文件仍可另存/新标签打开）
const previewSource = ref("");
let copyTimer: ReturnType<typeof setTimeout> | null = null;
async function copyMsg() {
  try {
    await navigator.clipboard.writeText(props.msg.content);
    copied.value = true;
    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(() => (copied.value = false), 1600);
  } catch {
    /* 剪贴板不可用时静默忽略 */
  }
}

function confirmHitl(choice: "confirmed" | "cancelled") {
  const sessionId = props.msg.hitl?.sessionId || "";
  chat.resume(choice, sessionId);
}

const editing = ref(false);
const editText = ref("");
function startEdit() {
  editText.value = props.msg.content;
  editing.value = true;
}
function cancelEdit() {
  editing.value = false;
}
function saveEdit() {
  const text = editText.value.trim();
  if (!text) return;
  chat.editAndResend(props.msg, text);
  editing.value = false;
}

/** 只有后面还有消息时才谈得上分支：最后一条没有可删除的后续内容 */
const canBranch = computed(() => {
  const idx = chat.messages.indexOf(props.msg);
  return idx >= 0 && idx < chat.messages.length - 1;
});
</script>

<template>
  <div
    class="group msg-in flex gap-3 py-2.5"
    :class="msg.role === 'user' ? 'flex-row-reverse' : ''"
  >
    <!-- 头像 -->
    <div
      class="mt-0.5 grid h-7 w-7 flex-shrink-0 place-items-center rounded-full text-2xs"
      :class="
        msg.role === 'user'
          ? avatarColor(auth.user).bg + ' font-semibold ' + avatarColor(auth.user).text
          : 'border border-line-2 bg-surface-2 text-ink-faint'
      "
    >
      <Icon v-if="msg.role === 'assistant'" name="agents" :size="13" />
      <span v-else>{{ userInitial }}</span>
    </div>

    <!-- 内容 -->
    <div class="min-w-0 max-w-[82%]">
      <!-- 用户消息 -->
      <template v-if="msg.role === 'user'">
        <div
          class="group/user relative rounded-2xl rounded-tr-md border px-4 py-2 text-base leading-relaxed transition"
          :class="
            editing
              ? 'border-accent/40 bg-surface'
              : // 按内容收缩并右对齐：否则下方操作条（时间+按钮）会把气泡撑得比文字宽
                'ml-auto w-fit border-line bg-surface-2'
          "
        >
          <!-- 编辑态：就地改写问题 -->
          <template v-if="editing">
            <textarea
              v-model="editText"
              rows="3"
              class="w-full resize-y rounded-xl border border-line-2 bg-surface px-3.5 py-2.5 text-base leading-relaxed text-ink outline-none transition placeholder:text-ink-faint focus:border-accent focus:ring-2 focus:ring-accent/15"
            />
            <div class="mt-2.5 flex items-center justify-between gap-2">
              <span class="text-2xs text-ink-faint">修改后将从此处重新生成回复</span>
              <div class="flex gap-2">
                <button
                  class="h-8 rounded-lg border border-line-2 px-3.5 text-xs text-ink-dim transition hover:border-line hover:text-ink active:scale-[0.98]"
                  @click="cancelEdit"
                >
                  取消
                </button>
                <button
                  class="flex h-8 items-center gap-1.5 rounded-lg bg-accent px-4 text-xs font-medium text-white transition hover:brightness-110 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
                  :disabled="chat.sending || !editText.trim()"
                  @click="saveEdit"
                >
                  <Icon name="send" :size="13" />
                  保存并重发
                </button>
              </div>
            </div>
          </template>
          <!-- 普通态：气泡内容 -->
          <div
            v-else
            ref="userBodyRef"
            class="md md-user"
            v-html="md.render(msg.content)"
            @click="onMarkdownClick"
          />
        </div>
        <!-- 操作条与助手消息一致：气泡下方、右侧对齐，悬停显示 -->
        <div
          v-if="!editing && !chat.sending"
          class="msg-actions mt-1 flex items-center justify-end gap-0.5"
        >
          <span v-if="msg.createdAt" class="mr-1 text-2xs tabular-nums text-ink-faint">
            {{ timeLabel(msg.createdAt) }}
          </span>
          <Tooltip :label="copied ? '已复制' : '复制'">
            <button
              class="flex h-6 w-6 items-center justify-center rounded-md text-ink-faint transition hover:bg-surface hover:text-ink"
              :aria-label="copied ? '已复制' : '复制'"
              @click="copyMsg"
            >
              <Icon :name="copied ? 'check' : 'copy'" :size="13" :class="copied ? 'text-ok' : ''" />
            </button>
          </Tooltip>
          <Tooltip label="编辑并重新发送">
            <button
              class="flex h-6 w-6 items-center justify-center rounded-md text-ink-faint transition hover:bg-surface hover:text-ink"
              aria-label="编辑并重新发送"
              @click="startEdit"
            >
              <Icon name="edit" :size="13" />
            </button>
          </Tooltip>
        </div>
      </template>

      <!-- 助手消息 -->
      <div v-else class="flex flex-col gap-1">
        <div class="rounded-2xl rounded-tl-md px-0.5 text-base leading-[1.7] text-ink">
          <div
            v-if="msg.content || msg.streaming"
            ref="bodyRef"
            class="md"
            v-html="html"
            @click="onMarkdownClick"
          />
          <div v-else class="text-ink-faint">正在思考…</div>
        </div>

        <!-- HITL 人工确认卡片 -->
        <div
          v-if="msg.hitl"
          class="mt-1.5 max-w-[520px] rounded-xl border border-warn/25 bg-warn/5 p-3.5"
        >
          <div class="mb-2.5 flex items-start gap-2 text-sm text-ink">
            <Icon name="warn" :size="15" class="mt-0.5 flex-shrink-0 text-warn" />
            <span>{{ msg.hitl.question }}</span>
          </div>
          <div class="flex gap-2">
            <button
              class="rounded-lg bg-ok px-3.5 py-1.5 text-xs font-medium text-white transition hover:brightness-110"
              @click="confirmHitl('confirmed')"
            >
              确认执行
            </button>
            <button
              class="rounded-lg border border-line-2 px-3.5 py-1.5 text-xs text-ink-dim transition hover:border-err/50 hover:text-err"
              @click="confirmHitl('cancelled')"
            >
              取消
            </button>
          </div>
        </div>

        <!-- Agent 编排轨道 -->
        <OrbitFlow v-if="msg.orbit?.length" :nodes="msg.orbit" :streaming="msg.streaming" />

        <!-- 引用溯源：RAG 检索命中的文档来源 -->
        <div v-if="msg.sources?.length" class="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span class="text-2xs text-ink-faint">来源 {{ msg.sources.length }}</span>
          <a
            v-for="(s, i) in msg.sources"
            :key="i"
            :data-source-index="i + 1"
            :href="docsApi.fileUrl(s.path)"
            target="_blank"
            rel="noreferrer"
            class="max-w-[200px] truncate rounded-full border border-line-2 px-2 py-0.5 text-2xs text-ink-dim transition hover:border-accent/50 hover:text-accent"
            :title="sourceTitle(s)"
            @click.prevent="previewSource = s.path"
          >
            {{ sourceName(s.path) }}
            <!-- 命中多段才标数量，避免噪音 -->
            <span v-if="s.hits && s.hits > 1" class="ml-1 text-ink-faint">×{{ s.hits }}</span>
          </a>
        </div>

        <!-- 操作按钮：复制 / 重新生成 -->
        <!-- hover 或键盘聚焦时出现；触屏没有 hover，常显（见 style.css 的 .msg-actions） -->
        <div
          v-if="!msg.streaming && msg.content"
          class="msg-actions mt-1 flex items-center gap-0.5"
        >
          <span v-if="msg.createdAt" class="mr-1 text-2xs tabular-nums text-ink-faint">
            {{ timeLabel(msg.createdAt) }}
          </span>
          <Tooltip :label="copied ? '已复制' : '复制'">
            <button
              class="flex h-6 w-6 items-center justify-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
              :aria-label="copied ? '已复制' : '复制'"
              @click="copyMsg"
            >
              <Icon :name="copied ? 'check' : 'copy'" :size="13" :class="copied ? 'text-ok' : ''" />
            </button>
          </Tooltip>
          <Tooltip v-if="!msg.hitl" label="重新生成">
            <button
              class="flex h-6 w-6 items-center justify-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="重新生成"
              :disabled="chat.sending"
              @click="chat.retry(msg)"
            >
              <Icon name="refresh" :size="13" />
            </button>
          </Tooltip>
          <Tooltip v-if="canBranch" label="从这里分支">
            <button
              class="flex h-6 w-6 items-center justify-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="从这里分支"
              :disabled="chat.sending"
              @click="chat.startBranch(msg)"
            >
              <Icon name="branch" :size="13" />
            </button>
          </Tooltip>
        </div>
      </div>
    </div>

    <SourcePreview v-if="previewSource" :source="previewSource" @close="previewSource = ''" />
  </div>
</template>
