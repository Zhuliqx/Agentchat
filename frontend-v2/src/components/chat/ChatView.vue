<script setup lang="ts">
import { computed, defineAsyncComponent, ref } from "vue";
import { sessionsApi } from "@/api";
import Icon from "@/components/common/Icon.vue";
import { useSessionsStore } from "@/stores/sessions";
import { useDocsStore } from "@/stores/docs";
import { useMemoryStore } from "@/stores/memory";
import ChatHeader from "./ChatHeader.vue";
import MessageList from "./MessageList.vue";
import ChatInput from "./ChatInput.vue";
import { useTaskAgentStore } from "@/stores/taskAgent";
import { useDialogStore } from "@/stores/dialog";
import { bootstrapActiveSession } from "@/composables/useSessionBootstrap";

const StatsModal = defineAsyncComponent(() => import("@/components/dialogs/StatsModal.vue"));
const TasksModal = defineAsyncComponent(() => import("@/components/dialogs/TasksModal.vue"));
const TaskAgentModal = defineAsyncComponent(
  () => import("@/components/dialogs/TaskAgentModal.vue"),
);

const sessions = useSessionsStore();
const docs = useDocsStore();
const memory = useMemoryStore();
const taskAgent = useTaskAgentStore();
const ui = useDialogStore();
const props = defineProps<{ showMenu?: boolean }>();
const emit = defineEmits<{ menu: [] }>();
const showStats = ref(false);
const showTasks = ref(false);

/** 首屏三个列表任一加载失败即提示，重试时只重跑失败的那个 */
const loadError = computed(() => sessions.error || docs.error || memory.error);
const retrying = ref(false);
async function retryLoad() {
  retrying.value = true;
  try {
    await Promise.all([
      sessions.error ? sessions.load() : null,
      docs.error ? docs.load() : null,
      memory.error ? memory.load() : null,
    ]);
    // 首屏失败时可能连当前会话都没建起来：重试成功后补上，避免停在"新会话"空页
    if (!sessions.error) await bootstrapActiveSession();
  } finally {
    retrying.value = false;
  }
}

async function exportSession() {
  if (!sessions.currentId) {
    await ui.alert("请先选择一个会话");
    return;
  }
  const r = await sessionsApi.exportMarkdown(sessions.currentId!);
  const blob = new Blob([r.markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${sessions.current?.title || "会话"}.md`;
  a.click();
  URL.revokeObjectURL(url);
}
</script>

<template>
  <main class="relative flex min-w-0 flex-1 flex-col">
    <!-- 首屏数据加载失败：给出原因和重试入口，后端恢复后不必整页刷新 -->
    <div
      v-if="loadError"
      role="status"
      class="flex flex-shrink-0 items-center gap-2 border-b border-warn/25 bg-warn/10 px-4 py-2 text-xs text-ink"
    >
      <Icon name="warn" :size="14" class="flex-shrink-0 text-warn" />
      <span class="min-w-0 flex-1 truncate" :title="loadError"
        >部分数据加载失败：{{ loadError }}</span
      >
      <button
        data-testid="retry-load"
        class="flex-shrink-0 rounded-md border border-line-2 px-2.5 py-1 text-2xs text-ink-dim transition hover:border-accent/50 hover:text-ink disabled:opacity-50"
        :disabled="retrying"
        @click="retryLoad"
      >
        {{ retrying ? "重试中…" : "重试" }}
      </button>
    </div>
    <ChatHeader
      :show-menu="props.showMenu"
      @menu="emit('menu')"
      @stats="showStats = true"
      @tasks="showTasks = true"
      @agenttask="taskAgent.openModal()"
      @export="exportSession"
    />
    <MessageList />
    <ChatInput />

    <StatsModal v-if="showStats" v-model="showStats" />
    <TasksModal v-if="showTasks" v-model="showTasks" />
    <TaskAgentModal v-if="taskAgent.open" />
  </main>
</template>
