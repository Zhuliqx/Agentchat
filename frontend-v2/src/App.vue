<script setup lang="ts">
import { onMounted, ref } from "vue";
import { healthApi } from "@/api";
import Sidebar from "@/components/Sidebar.vue";
import ChatView from "@/components/chat/ChatView.vue";
import AuthModal from "@/components/dialogs/AuthModal.vue";
import ProfileModal from "@/components/dialogs/ProfileModal.vue";
import Icon from "@/components/common/Icon.vue";
import { useAuthStore } from "@/stores/auth";
import { useSessionsStore } from "@/stores/sessions";
import { useDocsStore } from "@/stores/docs";
import { useMemoryStore } from "@/stores/memory";
import { useChatStore } from "@/stores/chat";

const auth = useAuthStore();
const sessions = useSessionsStore();
const docs = useDocsStore();
const memory = useMemoryStore();
const chat = useChatStore();

const healthOk = ref(false);
const healthText = ref("检查中…");
const showProfile = ref(false);

// 侧边栏显隐与宽度（持久化）
const SIDEBAR_MIN = 180;
const SIDEBAR_MAX = 480;
const sidebarOpen = ref(localStorage.getItem("sidebar-open") !== "0");
const sidebarWidth = ref(Number(localStorage.getItem("sidebar-width")) || 236);

function setSidebarOpen(v: boolean) {
  sidebarOpen.value = v;
  localStorage.setItem("sidebar-open", v ? "1" : "0");
}
function setSidebarWidth(w: number) {
  const clamped = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, w));
  sidebarWidth.value = clamped;
  localStorage.setItem("sidebar-width", String(clamped));
}

async function refreshHealth() {
  try {
    const h = await healthApi.get();
    healthOk.value = h.status === "ok";
    healthText.value = h.status === "ok" ? `服务正常 · MCP: ${h.mcp_servers.length} 个` : "部分组件异常";
  } catch {
    healthOk.value = false;
    healthText.value = "后端不可达";
  }
}

onMounted(async () => {
  await auth.init();
  refreshHealth();
  await Promise.all([sessions.load(), docs.load(), memory.load()]);
  if (!sessions.currentId) {
    if (sessions.list.length) {
      sessions.currentId = sessions.list[0].id;
      await chat.loadHistory(sessions.currentId);
    } else {
      await sessions.create();
      chat.clear();
    }
  }
});
</script>

<template>
  <div class="flex h-full">
    <!-- 折叠时：左侧浮动展开按钮 -->
    <button
      v-if="!sidebarOpen"
      class="fixed left-3 top-1/2 z-40 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full border border-line-2 bg-surface text-ink-dim shadow-[0_8px_24px_rgba(0,0,0,0.25)] transition hover:border-accent/50 hover:text-ink"
      title="展开侧边栏"
      aria-label="展开侧边栏"
      @click="setSidebarOpen(true)"
    >
      <Icon name="chevron" :size="16" class="-rotate-90" />
    </button>

    <Sidebar
      v-show="sidebarOpen"
      :width="sidebarWidth"
      :health-text="healthText"
      :health-ok="healthOk"
      @toggle="setSidebarOpen(!sidebarOpen)"
      @width-change="setSidebarWidth"
      @profile="showProfile = true"
    />
    <ChatView />
    <AuthModal />
    <ProfileModal v-model="showProfile" />
  </div>
</template>
