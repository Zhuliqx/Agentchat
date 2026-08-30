<script setup lang="ts">
import { ref } from "vue";
import { sessionsApi } from "@/api";
import { useChatStore } from "@/stores/chat";
import { useSessionsStore } from "@/stores/sessions";
import ChatHeader from "./ChatHeader.vue";
import MessageList from "./MessageList.vue";
import ChatInput from "./ChatInput.vue";
import StatsModal from "@/components/dialogs/StatsModal.vue";
import TasksModal from "@/components/dialogs/TasksModal.vue";
import TimeTravelModal from "@/components/dialogs/TimeTravelModal.vue";
import TaskAgentModal from "@/components/dialogs/TaskAgentModal.vue";
import { useTaskAgentStore } from "@/stores/taskAgent";

const chat = useChatStore();
const sessions = useSessionsStore();
const taskAgent = useTaskAgentStore();
const showStats = ref(false);
const showTasks = ref(false);
const showTimeTravel = ref(false);

async function exportSession() {
  if (!sessions.currentId) {
    alert("请先选择一个会话");
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

const headerRef = ref<InstanceType<typeof ChatHeader> | null>(null);
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

    <StatsModal v-model="showStats" />
    <TasksModal v-model="showTasks" />
    <TimeTravelModal v-model="showTimeTravel" />
    <TaskAgentModal />
  </main>
</template>
