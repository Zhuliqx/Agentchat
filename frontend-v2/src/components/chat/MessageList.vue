<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useChatStore } from "@/stores/chat";
import { useSessionsStore } from "@/stores/sessions";
import MessageItem from "./MessageItem.vue";
import Icon from "@/components/common/Icon.vue";
import Skeleton from "@/components/common/Skeleton.vue";
import { getReadMarker, setReadMarker } from "@/utils/readMarker";
import { relativeTime } from "@/utils/time";
import { useNow } from "@/composables/useNow";
import { useOpenSession } from "@/composables/useOpenSession";

const chat = useChatStore();
const sessions = useSessionsStore();
const listRef = ref<HTMLElement | null>(null);
const openSession = useOpenSession();
// 最近会话的相对时间要随分钟自己走，否则欢迎页停留久了会一直显示旧文案
const now = useNow();

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

// 流式输出时每个 token 都会触发 watcher：用 rAF 合并，避免每 token 同步读写
// scrollHeight/scrollTop（强制布局）导致掉帧；一帧最多滚一次。
let scrollRaf = 0;
function scheduleScrollBottom() {
  if (scrollRaf) return;
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0;
    scrollBottom();
  });
}
onBeforeUnmount(() => {
  if (scrollRaf) cancelAnimationFrame(scrollRaf);
});

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
    if (chat.atBottom) scheduleScrollBottom();
  },
  { flush: "post" },
);

// 切换会话（首条消息 id 变化）时重置为贴底，并滚到新会话最新位置
watch(
  () => chat.messages[0]?.id ?? "",
  (id, prev) => {
    if (id === prev) return;
    chat.markScroll(0);
    scheduleScrollBottom();
  },
);

// 输入区点击"回到最新"后滚到底部
watch(
  () => chat.scrollNonce,
  () => scheduleScrollBottom(),
);

const suggestions = [
  { icon: "doc", label: "知识库中有什么内容？" },
  { icon: "db", label: "帮我统计一下数据库里有多少个会话" },
  { icon: "globe", label: "搜索一下最近AI行业新闻" },
];

// 欢迎页"最近会话"：沿用侧栏排序取最近 3 条；当前这条是刚打开的空会话，不再列出
const recentSessions = computed(() =>
  sessions.list.filter((s) => s.id !== sessions.currentId).slice(0, 3),
);

// 能力卡片与后端实际挂载的 Agent/工具对齐（措辞取自 supervisor 的能力清单）
const capabilities = [
  { icon: "doc", name: "知识库检索", desc: "文档问答并给出引用" },
  { icon: "db", name: "数据库查询", desc: "会话、消息等业务数据" },
  { icon: "globe", name: "联网搜索", desc: "实时资讯与公开资料" },
  { icon: "brain", name: "长期记忆", desc: "跨会话记住你的偏好" },
  { icon: "zap", name: "代码执行", desc: "运行 Python 验证计算" },
  { icon: "shield", name: "人工确认", desc: "高风险操作先问过你" },
];

function ask(q: string) {
  chat.send(q);
}
</script>

<template>
  <div
    ref="listRef"
    data-msg-scroll
    class="min-h-0 flex-1 overflow-y-auto"
    role="log"
    aria-live="polite"
    aria-relevant="additions text"
    aria-label="会话消息"
    @scroll.passive="onScroll"
  >
    <div
      v-if="chat.historyError"
      class="content-col mt-4 rounded-lg bg-err/10 py-2 text-xs text-err"
    >
      {{ chat.historyError }}
    </div>

    <!-- 历史加载中：先给骨架，避免"欢迎页闪一下再变成历史" -->
    <div
      v-if="chat.historyLoading && !chat.messages.length"
      data-testid="history-skeleton"
      class="content-col flex flex-col gap-7 pt-10"
    >
      <div class="flex flex-col gap-2.5">
        <span class="h-3 w-28 animate-pulse rounded bg-surface-3" />
        <Skeleton :rows="3" />
      </div>
      <div class="flex flex-col gap-2.5">
        <span class="h-3 w-20 animate-pulse rounded bg-surface-3" />
        <Skeleton :rows="2" />
      </div>
      <span class="sr-only">正在加载会话记录…</span>
    </div>

    <!-- 欢迎页：能力总览 + 最近会话 + 示例问题 -->
    <!-- min-h-full：内容比视口高时容器跟着长，用父级滚动；h-full 会把顶部内容顶出可视区 -->
    <div
      v-else-if="!chat.messages.length"
      class="mx-auto flex min-h-full w-full max-w-[660px] flex-col justify-center px-4 pb-32 pt-10 sm:px-6"
    >
      <div class="flex flex-col items-center text-center">
        <div
          class="mb-4 grid h-12 w-12 place-items-center rounded-2xl border border-line-2 bg-surface-2 text-accent shadow-[0_0_0_1px_rgba(74,125,255,0.12)]"
        >
          <Icon name="agents" :size="22" />
        </div>
        <h2 class="mb-1.5 text-xl font-semibold tracking-tight">Multi-Agent 助手</h2>
        <p class="mb-6 max-w-[520px] text-sm leading-relaxed text-ink-dim">
          Supervisor 智能编排，自动路由到知识库、数据库与联网搜索等专业 Agent
        </p>
      </div>

      <!-- 能力卡片：纯信息，说明这套系统能接哪些活（动作入口在下方示例） -->
      <ul data-testid="welcome-capabilities" class="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <li
          v-for="c in capabilities"
          :key="c.name"
          class="flex items-start gap-2 rounded-xl border border-line bg-surface-2/40 px-3 py-2.5"
        >
          <Icon :name="c.icon" :size="14" class="mt-0.5 flex-shrink-0 text-accent" />
          <span class="min-w-0">
            <span class="block truncate text-sm font-medium">{{ c.name }}</span>
            <span class="block truncate text-2xs text-ink-faint">{{ c.desc }}</span>
          </span>
        </li>
      </ul>

      <!-- 最近会话：接着上次聊，省去在侧栏里找 -->
      <section v-if="recentSessions.length" data-testid="welcome-recent" class="mb-6">
        <div class="mb-1.5 px-1 text-2xs font-medium tracking-wide text-ink-faint">最近会话</div>
        <div class="flex flex-col gap-0.5">
          <button
            v-for="s in recentSessions"
            :key="s.id"
            :title="s.title || '新会话'"
            class="group flex items-center gap-2.5 rounded-lg px-3 py-2 text-left transition hover:bg-surface-2 coarse:min-h-10"
            @click="openSession(s.id)"
          >
            <Icon
              name="chat"
              :size="14"
              class="flex-shrink-0 text-ink-faint transition group-hover:text-accent"
            />
            <span
              class="min-w-0 flex-1 truncate text-sm text-ink-dim transition group-hover:text-ink"
              >{{ s.title || "新会话" }}</span
            >
            <span class="flex-shrink-0 text-2xs text-ink-faint">{{
              relativeTime(s.updated_at, now)
            }}</span>
          </button>
        </div>
      </section>

      <section data-testid="welcome-examples">
        <div class="mb-1.5 px-1 text-2xs font-medium tracking-wide text-ink-faint">试试这样问</div>
        <div class="flex flex-col gap-1">
          <button
            v-for="s in suggestions"
            :key="s.label"
            class="group flex items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 text-left text-sm text-ink-dim transition hover:border-line hover:bg-surface-2 hover:text-ink coarse:min-h-10"
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
      </section>
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
