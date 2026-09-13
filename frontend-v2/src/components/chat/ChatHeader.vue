<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { useSessionsStore } from "@/stores/sessions";
import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";
import Icon from "@/components/common/Icon.vue";
import Tooltip from "@/components/common/Tooltip.vue";
import { absoluteTime, relativeTime } from "@/utils/time";

const props = defineProps<{ showMenu?: boolean }>();
const emit = defineEmits<{
  menu: [];
  stats: [];
  tasks: [];
  agenttask: [];
  timetravel: [];
  export: [];
}>();

const sessions = useSessionsStore();
const auth = useAuthStore();
const chat = useChatStore();
const title = computed(() => sessions.current?.title || "新会话");

// 相对时间每分钟刷新一次（"刚刚"→"1 分钟前"），卸载时清理
const now = ref(Date.now());
const ticker = setInterval(() => (now.value = Date.now()), 60_000);
onBeforeUnmount(() => clearInterval(ticker));

/** 会话元信息："12 条消息 · 3 分钟前更新" */
const meta = computed(() => {
  const parts: string[] = [];
  if (chat.messages.length) parts.push(`${chat.messages.length} 条消息`);
  const updatedAt = sessions.current?.updated_at;
  if (updatedAt) parts.push(`${relativeTime(updatedAt, now.value)}更新`);
  return parts.join(" · ");
});
const metaTitle = computed(() => absoluteTime(sessions.current?.updated_at));

const iconBtn =
  "grid h-7 w-7 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink";
</script>

<template>
  <header
    class="flex h-[48px] flex-shrink-0 items-center border-b border-line bg-bg/80 backdrop-blur"
  >
    <!-- 与消息列同轴：共用 content-col 的宽度与左右留白 -->
    <div class="content-col flex min-w-0 items-center gap-1">
      <Tooltip v-if="props.showMenu" label="打开侧边栏">
        <button :class="iconBtn" aria-label="打开侧边栏" @click="emit('menu')">
          <Icon name="menu" :size="16" />
        </button>
      </Tooltip>
      <div class="min-w-0 flex-1 truncate">
        <h2 class="truncate text-sm font-medium tracking-tight text-ink">{{ title }}</h2>
      </div>
      <span
        v-if="meta"
        class="hidden flex-shrink-0 whitespace-nowrap pr-1 text-2xs text-ink-faint sm:inline"
        :title="metaTitle"
      >
        {{ meta }}
      </span>

      <!-- 会话操作：作用于当前会话 -->
      <div class="flex flex-shrink-0 items-center gap-0.5">
        <Tooltip label="版本历史">
          <button :class="iconBtn" aria-label="版本历史" @click="emit('timetravel')">
            <Icon name="clock" :size="15" />
          </button>
        </Tooltip>
        <Tooltip label="导出为 Markdown">
          <button :class="iconBtn" aria-label="导出为 Markdown" @click="emit('export')">
            <Icon name="export" :size="15" />
          </button>
        </Tooltip>
      </div>

      <span class="mx-0.5 h-4 w-px flex-shrink-0 bg-line-2" aria-hidden="true" />

      <!-- 平台工具：统计 / 定时任务 / 自主任务 Agent -->
      <div class="flex flex-shrink-0 items-center gap-0.5">
        <Tooltip label="会话数据分析">
          <button :class="iconBtn" aria-label="会话数据分析" @click="emit('stats')">
            <Icon name="stats" :size="15" />
          </button>
        </Tooltip>
        <Tooltip v-if="auth.platformOperator !== false" label="定时任务">
          <button :class="iconBtn" aria-label="定时任务" @click="emit('tasks')">
            <Icon name="tasks" :size="15" />
          </button>
        </Tooltip>
        <Tooltip label="自主任务 Agent">
          <button :class="iconBtn" aria-label="自主任务 Agent" @click="emit('agenttask')">
            <Icon name="sparkle" :size="15" />
          </button>
        </Tooltip>
      </div>
    </div>
  </header>
</template>
