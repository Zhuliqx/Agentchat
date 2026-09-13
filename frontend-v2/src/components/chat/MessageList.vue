<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useChatStore } from "@/stores/chat";
import { useSessionsStore } from "@/stores/sessions";
import MessageItem from "./MessageItem.vue";
import Icon from "@/components/common/Icon.vue";
import { getReadMarker, setReadMarker } from "@/utils/readMarker";

const chat = useChatStore();
const sessions = useSessionsStore();
const listRef = ref<HTMLElement | null>(null);

// 打开会话时快照"上次看到哪里"，本会话内不再变化（读完也不会让分隔线消失）。
// 历史是异步加载的：切到会话时消息还是空的，所以要等消息到齐后再算一次。
const dividerIndex = ref(-1);
let dividerSession = "";
watch(
  () => [sessions.currentId, chat.messages.length > 0] as const,
  ([sid, hasMessages]) => {
    if (!sid) {
      dividerSession = "";
      dividerIndex.value = -1;
      return;
    }
    if (dividerSession === sid) return; // 同一会话只算一次，新增消息不动分隔线
    if (!hasMessages) return; // 等历史到齐
    const marker = getReadMarker(sid);
    // 标记存的是后端消息 id（前端 id 每次刷新都会重新生成，不能用来定位）
    const idx = marker
      ? chat.messages.findIndex((m) => m.backendId === marker || m.id === marker)
      : -1;
    // 标记正好是最后一条 → 没有新消息，不画分隔线
    dividerIndex.value = idx >= 0 && idx < chat.messages.length - 1 ? idx + 1 : -1;
    dividerSession = sid;
  },
  { immediate: true },
);

// 用户在看最新内容（贴底）时推进已读位置，供下次打开时定位
watch(
  () => [chat.messages.length, chat.atBottom] as const,
  () => {
    // 分隔线还没算完就先别推进，否则会把"上次读到哪里"覆盖掉
    if (dividerSession !== sessions.currentId) return;
    const last = chat.messages[chat.messages.length - 1];
    // 优先记后端 id：它跨刷新稳定；尚未落库的消息退回前端 id
    if (sessions.currentId && last && chat.atBottom)
      setReadMarker(sessions.currentId, last.backendId || last.id);
  },
  { immediate: true },
);

function scrollBottom() {
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight;
}

function onScroll() {
  const el = listRef.value;
  if (!el) return;
  // 贴底判定与新消息计数由 store 统一维护（输入区的"回到最新"按钮也要用）
  chat.markScroll(el.scrollHeight - el.scrollTop - el.clientHeight);
}

const lastMessage = computed(() => chat.messages[chat.messages.length - 1]);

// 只监听“最后一条”的流式变化：避免每个 token 都对全部消息做 map+join
watch(
  () => [chat.messages.length, lastMessage.value?.content, lastMessage.value?.streaming] as const,
  () => {
    if (chat.atBottom) nextTick(scrollBottom);
  },
  { flush: "post" },
);

// 切换会话（首条消息 id 变化）时重置为贴底，并滚到新会话最新位置
watch(
  () => chat.messages[0]?.id ?? "",
  (id, prev) => {
    if (id === prev) return;
    chat.markScroll(0);
    nextTick(scrollBottom);
  },
);

// 输入区点击"回到最新"后滚到底部
watch(
  () => chat.scrollNonce,
  () => nextTick(scrollBottom),
);

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
      class="content-col mt-4 rounded-lg bg-err/10 py-2 text-xs text-err"
    >
      {{ chat.historyError }}
    </div>

    <!-- 欢迎页 -->
    <div
      v-if="!chat.messages.length"
      class="mx-auto flex h-full max-w-[620px] flex-col justify-center px-4 pb-24 sm:px-6"
    >
      <div class="flex flex-col items-center text-center">
        <div
          class="mb-5 grid h-12 w-12 place-items-center rounded-2xl border border-line-2 bg-surface-2 text-accent shadow-[0_0_0_1px_rgba(74,125,255,0.12)]"
        >
          <Icon name="agents" :size="22" />
        </div>
        <h2 class="mb-1.5 text-xl font-semibold tracking-tight">Multi-Agent 助手</h2>
        <p class="mb-8 max-w-[380px] text-sm leading-relaxed text-ink-dim">
          Supervisor 智能编排，自动路由到知识库、数据库与联网搜索等专业 Agent
        </p>
      </div>

      <div class="flex flex-col gap-1">
        <button
          v-for="s in suggestions"
          :key="s.label"
          class="group flex items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 text-left text-sm text-ink-dim transition hover:border-line hover:bg-surface-2 hover:text-ink"
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
    <div v-else class="content-col flex flex-col gap-1 pb-32 pt-6">
      <template v-for="(m, i) in chat.messages" :key="m.id">
        <div v-if="i === dividerIndex" class="my-2 flex items-center gap-2" data-new-divider>
          <span class="h-px flex-1 bg-accent/40" />
          <span class="text-2xs text-accent">以下为新消息</span>
          <span class="h-px flex-1 bg-accent/40" />
        </div>
        <MessageItem :msg="m" />
      </template>
    </div>
  </div>
</template>
