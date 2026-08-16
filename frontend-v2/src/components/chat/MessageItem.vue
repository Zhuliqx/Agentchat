<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useThrottleFn } from "@vueuse/core";
import { useChatStore, type ChatMsg } from "@/stores/chat";
import { md } from "@/utils/markdown";
import { useAuthStore } from "@/stores/auth";
import OrbitFlow from "./OrbitFlow.vue";
import Icon from "@/components/common/Icon.vue";

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
          ? 'bg-accent/18 font-semibold text-accent'
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
        class="rounded-2xl rounded-tr-md border border-line bg-surface-2 px-4 py-2.5 text-[13.5px] leading-relaxed"
      >
        <div class="md" v-html="md.render(msg.content)" />
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
        </div>
      </div>
    </div>
  </div>
</template>
