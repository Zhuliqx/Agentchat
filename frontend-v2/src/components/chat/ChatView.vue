<script setup lang="ts">
import { defineAsyncComponent, ref } from "vue";
import { sessionsApi } from "@/api";
import { useSessionsStore } from "@/stores/sessions";
import ChatHeader from "./ChatHeader.vue";
import MessageList from "./MessageList.vue";
import ChatInput from "./ChatInput.vue";
import { useTaskAgentStore } from "@/stores/taskAgent";
import { useDialogStore } from "@/stores/dialog";

const StatsModal = defineAsyncComponent(() => import("@/components/dialogs/StatsModal.vue"));
const TasksModal = defineAsyncComponent(() => import("@/components/dialogs/TasksModal.vue"));
const TimeTravelModal = defineAsyncComponent(
  () => import("@/components/dialogs/TimeTravelModal.vue"),
);
const TaskAgentModal = defineAsyncComponent(
  () => import("@/components/dialogs/TaskAgentModal.vue"),
);

const sessions = useSessionsStore();
const taskAgent = useTaskAgentStore();
const ui = useDialogStore();
const showStats = ref(false);
const showTasks = ref(false);
const showTimeTravel = ref(false);

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
    <ChatHeader
      @stats="showStats = true"
      @tasks="showTasks = true"
      @agenttask="taskAgent.openModal()"
      @timetravel="showTimeTravel = true"
      @export="exportSession"
    />
    <MessageList />
    <ChatInput />

    <StatsModal v-if="showStats" v-model="showStats" />
    <TasksModal v-if="showTasks" v-model="showTasks" />
    <TimeTravelModal v-if="showTimeTravel" v-model="showTimeTravel" />
    <TaskAgentModal v-if="taskAgent.open" />
  </main>
</template>
