<script setup lang="ts">
import { computed } from "vue";
import { useSessionsStore } from "@/stores/sessions";
import Icon from "@/components/common/Icon.vue";

const emit = defineEmits<{
  stats: [];
  tasks: [];
  timetravel: [];
  export: [];
}>();

const sessions = useSessionsStore();
const title = computed(() => sessions.current?.title || "新会话");
</script>

<template>
  <header class="flex h-[48px] flex-shrink-0 items-center gap-1 border-b border-line bg-bg/80 px-3 backdrop-blur">
    <div class="min-w-0 flex-1 truncate px-2">
      <h2 class="truncate text-[13.5px] font-medium tracking-tight text-ink">{{ title }}</h2>
    </div>

    <!-- 工具按钮（图标化） -->
    <div class="flex flex-shrink-0 items-center gap-0.5">
      <button
        class="grid h-7 w-7 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
        title="会话数据分析"
        @click="emit('stats')"
      >
        <Icon name="stats" :size="15" />
      </button>
      <button
        class="grid h-7 w-7 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
        title="定时任务"
        @click="emit('tasks')"
      >
        <Icon name="tasks" :size="15" />
      </button>
      <button
        class="grid h-7 w-7 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
        title="版本历史"
        @click="emit('timetravel')"
      >
        <Icon name="clock" :size="15" />
      </button>
      <button
        class="grid h-7 w-7 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
        title="导出为 Markdown"
        @click="emit('export')"
      >
        <Icon name="export" :size="15" />
      </button>
    </div>
  </header>
</template>
