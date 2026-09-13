<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref, watch } from "vue";
import { useMediaQuery, useWindowSize } from "@vueuse/core";
import { healthApi } from "@/api";
import Sidebar from "@/components/Sidebar.vue";
import ChatView from "@/components/chat/ChatView.vue";
import AuthModal from "@/components/dialogs/AuthModal.vue";
import AppDialog from "@/components/common/AppDialog.vue";
import Icon from "@/components/common/Icon.vue";
import { useAuthStore } from "@/stores/auth";
import { useSessionsStore } from "@/stores/sessions";
import { useDocsStore } from "@/stores/docs";
import { useMemoryStore } from "@/stores/memory";
import { clampSidebarWidth } from "@/utils/sidebarLayout";
import { bootstrapActiveSession } from "@/composables/useSessionBootstrap";

const ProfileModal = defineAsyncComponent(() => import("@/components/dialogs/ProfileModal.vue"));
const AdminModal = defineAsyncComponent(() => import("@/components/dialogs/AdminModal.vue"));

const auth = useAuthStore();
const sessions = useSessionsStore();
const docs = useDocsStore();
const memory = useMemoryStore();

const healthOk = ref(false);
const healthText = ref("检查中…");
const showProfile = ref(false);
const showAdmin = ref(false);

// 侧边栏显隐与宽度（持久化）：宽屏内嵌开合，窄屏改为抽屉
const sidebarOpen = ref(localStorage.getItem("sidebar-open") !== "0");
const sidebarWidth = ref(clampSidebarWidth(localStorage.getItem("sidebar-width")));
const compact = useMediaQuery("(max-width: 1023px)");
const { width: viewportWidth } = useWindowSize();
const drawerOpen = ref(false);
const drawerWidth = computed(() => Math.min(320, Math.round(viewportWidth.value * 0.86)));
const sidebarVisible = computed(() => (compact.value ? drawerOpen.value : sidebarOpen.value));
// 抽屉宽度固定，内嵌形态用持久化宽度；折叠时都为 0（交给过渡动画收拢）
const sidebarContentWidth = computed(() =>
  compact.value ? drawerWidth.value : sidebarWidth.value,
);
const sidebarVisibleWidth = computed(() => (sidebarVisible.value ? sidebarContentWidth.value : 0));

// 进出窄屏时收起抽屉，避免宽屏下残留悬浮侧栏
watch(compact, (isCompact) => {
  if (isCompact) drawerOpen.value = false;
});
// 抽屉里切换/新建会话后自动收起，避免遮住刚打开的对话
watch(
  () => sessions.currentId,
  () => {
    if (compact.value) drawerOpen.value = false;
  },
);

function setSidebarOpen(v: boolean) {
  if (compact.value) {
    drawerOpen.value = v;
    return;
  }
  sidebarOpen.value = v;
  localStorage.setItem("sidebar-open", v ? "1" : "0");
}
function setSidebarWidth(w: number) {
  const clamped = clampSidebarWidth(w);
  sidebarWidth.value = clamped;
  localStorage.setItem("sidebar-width", String(clamped));
}

async function refreshHealth() {
  try {
    const h = await healthApi.get();
    healthOk.value = h.status === "ok";
    healthText.value =
      h.status === "ok" ? `服务正常 · MCP: ${h.mcp_servers.length} 个` : "部分组件异常";
  } catch {
    healthOk.value = false;
    healthText.value = "后端不可达";
  }
}

onMounted(async () => {
  // token 过期/失效（任意 API 返回 401）→ 清空登录态并弹出登录框
  window.addEventListener("auth-expired", () => {
    auth.logoutLocal();
    auth.loadCapabilities();
    auth.openAuth("login");
  });
  await Promise.all([auth.init(), auth.loadCapabilities()]);
  refreshHealth();
  await Promise.all([sessions.load(), docs.load(), memory.load()]);
  try {
    await bootstrapActiveSession();
  } catch (e) {
    // 后端不可达时连"新建会话"也会失败：交给顶部告警条提示，避免未捕获的 promise
    sessions.error = (e as Error).message || "初始化会话失败";
  }
});
</script>

<template>
  <div class="flex h-full">
    <!-- 窄屏抽屉遮罩：点击关闭 -->
    <div
      v-if="compact && drawerOpen"
      class="fixed inset-0 z-30 bg-black/50 backdrop-blur-[1px]"
      @click="drawerOpen = false"
    />

    <!-- 宽屏折叠时：左侧浮动展开按钮（延迟淡入，避免与仍在收拢的侧栏重叠） -->
    <button
      v-if="!compact"
      class="fixed left-3 top-1/2 z-40 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full border border-line-2 bg-surface text-ink-dim shadow-[0_8px_24px_rgba(0,0,0,0.25)] transition-[opacity,transform,border-color,color] duration-200 ease-out hover:border-accent/50 hover:text-ink"
      :class="
        sidebarVisible
          ? 'pointer-events-none -translate-x-2 opacity-0'
          : 'delay-150 hover:scale-105'
      "
      :tabindex="sidebarVisible ? -1 : 0"
      :aria-hidden="sidebarVisible"
      title="展开侧边栏"
      aria-label="展开侧边栏"
      @click="setSidebarOpen(true)"
    >
      <Icon name="chevron" :size="16" class="-rotate-90" />
    </button>

    <Sidebar
      :width="sidebarVisibleWidth"
      :content-width="sidebarContentWidth"
      :open="sidebarVisible"
      :overlay="compact"
      :health-text="healthText"
      :health-ok="healthOk"
      @toggle="setSidebarOpen(!sidebarVisible)"
      @width-change="setSidebarWidth"
      @profile="showProfile = true"
      @admin="showAdmin = true"
    />
    <ChatView :show-menu="compact" @menu="drawerOpen = true" />
    <AuthModal />
    <AppDialog />
    <ProfileModal v-if="showProfile" v-model="showProfile" />
    <AdminModal v-if="showAdmin" v-model="showAdmin" />
  </div>
</template>
