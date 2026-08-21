<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useThrottleFn } from "@vueuse/core";
import { useChatStore, type ChatMsg } from "@/stores/chat";
import { md } from "@/utils/markdown";
import { useAuthStore } from "@/stores/auth";
import { docsApi } from "@/api";
import OrbitFlow from "./OrbitFlow.vue";
import Icon from "@/components/common/Icon.vue";
import { avatarColor } from "@/utils/avatar";

const props = defineProps<{ msg: ChatMsg }>();
const chat = useChatStore();
const auth = useAuthStore();

const userInitial = computed(() => (auth.user?.username || "我").slice(0, 1).toUpperCase());

const html = ref("");
const render = useThrottleFn(
  () => {
    html.value = props.msg.streaming
      ? md.renderStream(props.msg.content)
      : md.render(props.msg.content);
  },
  60,
  false
);
watch(
  () => [props.msg.content, props.msg.streaming],
  render,
  { immediate: true }
);

const copied = ref(false);
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

function deleteMsg() {
  if (chat.sending) return;
  if (!confirm("确定删除这条消息？")) return;
  chat.deleteMessage(props.msg);
}
</script>

<template>
  <div
    class="group msg-in flex gap-3 py-2.5"
    :class="msg.role === 'user' ? 'flex-row-reverse' : ''"
  >
    <!-- 头像 -->
    <div
      class="mt-0.5 grid h-7 w-7 flex-shrink-0 place-items-center rounded-full text-[11px]"
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
      <div
        v-if="msg.role === 'user'"
        class="group/user relative rounded-2xl rounded-tr-md border px-4 py-2.5 text-[13.5px] leading-relaxed transition"
        :class="editing ? 'border-accent/40 bg-surface' : 'border-line bg-surface-2'"
      >
        <!-- 编辑态：就地改写问题 -->
        <template v-if="editing">
          <textarea
            v-model="editText"
            rows="3"
            class="w-full resize-y rounded-xl border border-line-2 bg-surface px-3.5 py-2.5 text-[13.5px] leading-relaxed text-ink outline-none transition placeholder:text-ink-faint/70 focus:border-accent focus:ring-2 focus:ring-accent/15"
          />
          <div class="mt-2.5 flex items-center justify-between gap-2">
            <span class="text-[11px] text-ink-faint">修改后将从此处重新生成回复</span>
            <div class="flex gap-2">
              <button
                class="h-8 rounded-lg border border-line-2 px-3.5 text-[12.5px] text-ink-dim transition hover:border-line hover:text-ink active:scale-[0.98]"
                @click="cancelEdit"
              >
                取消
              </button>
              <button
                class="flex h-8 items-center gap-1.5 rounded-lg bg-accent px-4 text-[12.5px] font-medium text-white transition hover:brightness-110 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
                :disabled="chat.sending || !editText.trim()"
                @click="saveEdit"
              >
                <Icon name="send" :size="13" />
                保存并重发
              </button>
            </div>
          </div>
        </template>
        <!-- 普通态：内容 + hover 编辑/删除按钮 -->
        <template v-else>
          <div class="md" v-html="md.render(msg.content)" />
          <div
            v-if="!chat.sending"
            class="absolute -left-8 top-0.5 flex flex-col gap-0.5 opacity-0 transition group-hover/user:opacity-100"
          >
            <button
              class="grid h-6 w-6 place-items-center rounded-md text-ink-faint transition hover:bg-surface hover:text-ink"
              title="编辑并重新发送"
              @click="startEdit"
            >
              <Icon name="edit" :size="13" />
            </button>
            <button
              class="grid h-6 w-6 place-items-center rounded-md text-ink-faint transition hover:bg-err/10 hover:text-err"
              title="删除消息"
              @click="deleteMsg"
            >
              <Icon name="trash" :size="13" />
            </button>
          </div>
        </template>
      </div>

      <!-- 助手消息 -->
      <div v-else class="flex flex-col gap-1">
        <div class="rounded-2xl rounded-tl-md px-0.5 text-[13.5px] leading-[1.7] text-ink">
          <div v-if="msg.content || msg.streaming" class="md" :class="{ 'typing-caret': msg.streaming }" v-html="html" />
          <div v-else class="text-ink-faint">正在思考…</div>
        </div>

        <!-- HITL 人工确认卡片 -->
        <div v-if="msg.hitl" class="mt-1.5 max-w-[520px] rounded-xl border border-warn/25 bg-warn/5 p-3.5">
          <div class="mb-2.5 flex items-start gap-2 text-[13px] text-ink">
            <Icon name="warn" :size="15" class="mt-0.5 flex-shrink-0 text-warn" />
            <span>{{ msg.hitl.question }}</span>
          </div>
          <div class="flex gap-2">
            <button
              class="rounded-lg bg-ok px-3.5 py-1.5 text-[12px] font-medium text-white transition hover:brightness-110"
              @click="confirmHitl('confirmed')"
            >
              确认执行
            </button>
            <button
              class="rounded-lg border border-line-2 px-3.5 py-1.5 text-[12px] text-ink-dim transition hover:border-err/50 hover:text-err"
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
          <span class="text-[10.5px] text-ink-faint">来源</span>
          <a
            v-for="(s, i) in msg.sources"
            :key="i"
            :href="docsApi.fileUrl(s)"
            target="_blank"
            rel="noreferrer"
            class="max-w-[200px] truncate rounded-full border border-line-2 px-2 py-0.5 text-[10.5px] text-ink-dim transition hover:border-accent/50 hover:text-accent"
            :title="s"
          >
            {{ s.split("/").pop() }}
          </a>
        </div>

        <!-- 操作按钮：复制 / 重新生成 -->
        <div v-if="!msg.streaming && msg.content" class="mt-1 flex items-center gap-0.5">
          <button
            class="flex h-6 w-6 items-center justify-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
            :title="copied ? '已复制' : '复制'"
            @click="copyMsg"
          >
            <Icon :name="copied ? 'check' : 'copy'" :size="13" :class="copied ? 'text-ok' : ''" />
          </button>
          <button
            v-if="!msg.hitl"
            class="flex h-6 w-6 items-center justify-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
            title="重新生成"
            :disabled="chat.sending"
            @click="chat.retry(msg)"
          >
            <Icon name="refresh" :size="13" />
          </button>
          <button
            class="flex h-6 w-6 items-center justify-center rounded-md text-ink-faint transition hover:bg-err/10 hover:text-err disabled:cursor-not-allowed disabled:opacity-40"
            title="删除消息"
            :disabled="chat.sending"
            @click="deleteMsg"
          >
            <Icon name="trash" :size="13" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
