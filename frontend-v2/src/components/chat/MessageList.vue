<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useChatStore } from "@/stores/chat";
import MessageItem from "./MessageItem.vue";
import Icon from "@/components/common/Icon.vue";

const chat = useChatStore();
const listRef = ref<HTMLElement | null>(null);
const stickToBottom = ref(true);

function scrollBottom() {
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight;
}

function onScroll() {
  const el = listRef.value;
  if (!el) return;
  stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight <= 40;
}

const lastMessage = computed(() => chat.messages[chat.messages.length - 1]);

// 只监听“最后一条”的流式变化：避免每个 token 都对全部消息做 map+join
watch(
  () => [chat.messages.length, lastMessage.value?.content, lastMessage.value?.streaming] as const,
  () => {
    if (stickToBottom.value) nextTick(scrollBottom);
  },
  { flush: "post" },
);

// 切换会话（首条消息 id 变化）时重置为贴底，并滚到新会话最新位置
watch(
  () => chat.messages[0]?.id ?? "",
  (id, prev) => {
    if (id === prev) return;
    stickToBottom.value = true;
    nextTick(scrollBottom);
  },
);

function jumpToBottom() {
  stickToBottom.value = true;
  nextTick(scrollBottom);
}

const suggestions = [
  { icon: "doc", label: "知识库中有什么内容？" },
  { icon: "db", label: "帮我统计一下数据库里有多少个会话" },
  { icon: "globe", label: "搜索一下最近AI行业新闻" },
];

function ask(q: string) {
  chat.send(q);
}
</script>

<template>
  <div ref="listRef" class="min-h-0 flex-1 overflow-y-auto" @scroll.passive="onScroll">
    <div
      v-if="chat.historyError"
      class="mx-auto mt-4 max-w-[760px] rounded-lg bg-err/10 px-3 py-2 text-[12.5px] text-err"
    >
      {{ chat.historyError }}
    </div>

    <!-- 欢迎页 -->
    <div
      v-if="!chat.messages.length"
      class="mx-auto flex h-full max-w-[620px] flex-col justify-center px-6 pb-24"
    >
      <div class="flex flex-col items-center text-center">
        <div
          class="mb-5 grid h-12 w-12 place-items-center rounded-2xl border border-line-2 bg-surface-2 text-accent shadow-[0_0_0_1px_rgba(74,125,255,0.12)]"
        >
          <Icon name="agents" :size="22" />
        </div>
        <h2 class="mb-1.5 text-[19px] font-semibold tracking-tight">Multi-Agent 助手</h2>
        <p class="mb-8 max-w-[380px] text-[13px] leading-relaxed text-ink-dim">
          Supervisor 智能编排，自动路由到知识库、数据库与联网搜索等专业 Agent
        </p>
      </div>

      <div class="flex flex-col gap-1">
        <button
          v-for="s in suggestions"
          :key="s.label"
          class="group flex items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 text-left text-[13px] text-ink-dim transition hover:border-line hover:bg-surface-2 hover:text-ink"
          @click="ask(s.label)"
        >
          <Icon
            :name="s.icon"
            :size="15"
            class="flex-shrink-0 text-ink-faint transition group-hover:text-accent"
          />
          <span class="min-w-0 flex-1 truncate">{{ s.label }}</span>
          <Icon
            name="chevron"
            :size="13"
            class="flex-shrink-0 -rotate-90 text-ink-faint transition group-hover:text-ink-dim"
          />
        </button>
      </div>
    </div>

    <!-- 消息列表（底部留白给悬浮输入框） -->
    <div v-else class="mx-auto flex max-w-[760px] flex-col gap-1 px-6 pb-32 pt-6">
      <MessageItem v-for="m in chat.messages" :key="m.id" :msg="m" />
    </div>

    <button
      v-if="!stickToBottom"
      class="sticky bottom-28 left-1/2 z-10 flex h-8 -translate-x-1/2 items-center gap-1 rounded-full border border-line-2 bg-surface px-3 text-[11.5px] text-ink-dim shadow-[0_8px_24px_rgba(0,0,0,0.35)] transition hover:border-accent/50 hover:text-ink"
      @click="jumpToBottom"
    >
      <Icon name="chevron" :size="12" class="rotate-180" />
      回到最新
    </button>
  </div>
</template>
