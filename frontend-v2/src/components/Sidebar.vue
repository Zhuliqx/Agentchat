<script setup lang="ts">
import { computed, ref } from "vue";
import { useSessionsStore } from "@/stores/sessions";
import { useAuthStore } from "@/stores/auth";
import { useDocsStore } from "@/stores/docs";
import { useMemoryStore } from "@/stores/memory";
import { useThemeStore } from "@/stores/theme";
import { useChatStore } from "@/stores/chat";
import { useChatOptionsStore } from "@/stores/chatOptions";
import SessionList from "./sidebar/SessionList.vue";
import DocPanel from "./sidebar/DocPanel.vue";
import MemoryPanel from "./sidebar/MemoryPanel.vue";
import UserMenu from "./sidebar/UserMenu.vue";
import Icon from "@/components/common/Icon.vue";
import Switch from "@/components/common/Switch.vue";

const props = defineProps<{ healthText: string; healthOk: boolean; width?: number }>();
const emit = defineEmits<{
  profile: [];
  toggle: [];
  "width-change": [w: number];
}>();
const sessions = useSessionsStore();
const auth = useAuthStore();
const docs = useDocsStore();
const memory = useMemoryStore();
const theme = useThemeStore();
const chat = useChatStore();
const options = useChatOptionsStore();
const docsCount = computed(() => docs.list.length);
const memoryCount = computed(() => memory.list.length);

// 折叠状态
const folded = ref<Record<string, boolean>>({
  sessions: localStorage.getItem("fold-sessions") === "1",
  docs: localStorage.getItem("fold-docs") === "1",
  memory: localStorage.getItem("fold-memory") === "1",
});
function toggle(key: string) {
  folded.value[key] = !folded.value[key];
  localStorage.setItem(`fold-${key}`, folded.value[key] ? "1" : "0");
}

async function newSession() {
  await sessions.create();
  // 切换到新会话：清空聊天区（避免仍显示旧会话消息）
  chat.clear();
}

// ---- 拖拽调整宽度（收窄自动折叠；折叠后不松手反向拖拽自动展开） ----
const FOLD_THRESHOLD = 180; // 折叠 / 展开共用阈值（收窄到该值以下折叠，反向拖回该值及以上展开）
const MIN_WIDTH = 180;
function startResize(e: MouseEvent) {
  e.preventDefault();
  const startX = e.clientX;
  const startW = props.width ?? 236;
  let collapsed = false; // 本次拖拽中是否已触发折叠
  let done = false;
  const end = () => {
    if (done) return;
    done = true;
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
  };
  const onMove = (ev: MouseEvent) => {
    const raw = startW + ev.clientX - startX;
    if (!collapsed) {
      // 正常拖拽：收窄到阈值以下 → 折叠（不结束，等待反向拖拽）
      if (raw < FOLD_THRESHOLD) {
        collapsed = true;
        emit("toggle");
        return;
      }
      emit("width-change", Math.max(MIN_WIDTH, raw));
    } else {
      // 已折叠：向右拖回阈值及以上 → 自动展开并继续调整宽度
      if (raw >= FOLD_THRESHOLD) {
        collapsed = false;
        emit("toggle");
        emit("width-change", raw);
      }
      // 折叠中向左/小幅移动：保持折叠，不更新宽度
    }
  };
  const onUp = () => end();
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}
</script>

<template>
  <aside
    class="relative flex flex-col border-r border-line bg-surface"
    :style="{ width: (width ?? 236) + 'px', minWidth: (width ?? 236) + 'px' }"
  >
    <!-- 品牌 + 收起 -->
    <div class="flex items-center gap-2.5 px-4 pt-4 pb-3">
      <div class="grid h-8 w-8 flex-shrink-0 place-items-center rounded-[9px] bg-accent/15 text-accent">
        <Icon name="agents" :size="16" />
      </div>
      <div class="min-w-0">
        <h1 class="text-[13.5px] font-semibold leading-tight tracking-tight">Multi-Agent</h1>
        <span class="block truncate text-[10.5px] text-ink-faint">RAG · MCP · LangGraph</span>
      </div>
      <button
        class="ml-auto grid h-6 w-6 flex-shrink-0 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
        title="收起侧边栏"
        aria-label="收起侧边栏"
        @click="emit('toggle')"
      >
        <Icon name="chevron" :size="14" class="rotate-90" />
      </button>
    </div>

    <!-- 新建会话 -->
    <div class="px-3 pb-2">
      <button
        class="flex w-full items-center gap-2 rounded-lg border border-line-2 bg-surface-2 px-3 py-[7px] text-[12.5px] font-medium text-ink transition hover:border-accent/50 hover:bg-surface-3"
        @click="newSession"
      >
        <Icon name="plus" :size="14" />
        新建会话
      </button>
    </div>

    <!-- 会话区：弹性铺满剩余空间，列表滚动 -->
    <div class="no-scrollbar flex min-h-0 flex-1 flex-col px-2.5 pb-1">
      <section class="mb-1 flex min-h-0 flex-1 flex-col">
        <div class="flex items-center gap-1.5 px-1.5 py-1.5">
          <button class="flex items-center gap-1 text-[10.5px] font-medium uppercase tracking-[0.08em] text-ink-faint transition hover:text-ink-dim" @click="toggle('sessions')">
            <Icon
              name="chevron"
              :size="11"
              class="transition-transform"
              :class="folded.sessions ? '-rotate-90' : ''"
            />
            {{ sessions.batchMode ? "批量选择" : "会话" }}
            <span class="ml-0.5 rounded bg-surface-3 px-1 py-px text-[10px] font-normal text-ink-dim">{{ sessions.list.length }}</span>
          </button>
          <button
            class="ml-auto text-ink-faint transition hover:text-ink-dim"
            :title="sessions.batchMode ? '完成' : '多选'"
            @click="sessions.toggleBatch()"
          >
            <Icon v-if="sessions.batchMode" name="check" :size="13" />
            <Icon v-else name="dots" :size="13" />
          </button>
        </div>
        <div v-if="sessions.batchMode" class="mb-1 flex gap-1 px-1.5">
          <button class="rounded border border-line-2 px-1.5 py-0.5 text-[10.5px] text-ink-dim hover:text-ink" @click="sessions.toggleSelectAll()">全选</button>
          <button
            class="rounded border border-err/40 px-1.5 py-0.5 text-[10.5px] text-err disabled:opacity-40"
            :disabled="!sessions.selected.size"
            @click="sessions.batchDelete([...sessions.selected])"
          >
            删除{{ sessions.selected.size ? ` (${sessions.selected.size})` : "" }}
          </button>
        </div>
        <div v-if="!folded.sessions" class="no-scrollbar min-h-0 flex-1 overflow-y-auto">
          <SessionList />
        </div>
      </section>
    </div>

    <!-- 文档 + 记忆：固定在底部（始终可见，各自区域内滚动） -->
    <div class="flex-shrink-0 border-t border-line px-2.5 pt-1">
      <!-- 知识库文档 -->
      <section class="mb-1">
        <div class="flex items-center">
          <button
            class="flex min-w-0 flex-1 items-center gap-1 px-1.5 py-1.5 text-left text-[10.5px] font-medium uppercase tracking-[0.08em] text-ink-faint transition hover:text-ink-dim"
            @click="toggle('docs')"
          >
            <Icon
              name="chevron"
              :size="11"
              class="flex-shrink-0 transition-transform"
              :class="folded.docs ? '-rotate-90' : ''"
            />
            <span class="min-w-0 flex-1 truncate">文档</span>
            <span class="ml-0.5 rounded bg-surface-3 px-1 py-px text-[10px] font-normal text-ink-dim">{{ docsCount }}</span>
          </button>
          <Switch
            class="flex-shrink-0"
            :model-value="options.useRag"
            title="问答时使用知识库"
            @update:model-value="(v: boolean) => (options.useRag = v)"
          />
        </div>
        <div v-if="!folded.docs" class="no-scrollbar max-h-[22vh] overflow-y-auto">
          <DocPanel />
        </div>
      </section>

      <!-- 长期记忆 -->
      <section>
        <div class="flex items-center">
          <button
            class="flex min-w-0 flex-1 items-center gap-1 px-1.5 py-1.5 text-left text-[10.5px] font-medium uppercase tracking-[0.08em] text-ink-faint transition hover:text-ink-dim"
            @click="toggle('memory')"
          >
            <Icon
              name="chevron"
              :size="11"
              class="flex-shrink-0 transition-transform"
              :class="folded.memory ? '-rotate-90' : ''"
            />
            <span class="min-w-0 flex-1 truncate">记忆</span>
            <span class="ml-0.5 rounded bg-surface-3 px-1 py-px text-[10px] font-normal text-ink-dim">{{ memoryCount }}</span>
          </button>
          <Switch
            class="flex-shrink-0"
            :model-value="options.useMemory"
            title="问答时使用长期记忆"
            @update:model-value="(v: boolean) => (options.useMemory = v)"
          />
        </div>
        <div v-if="!folded.memory" class="no-scrollbar max-h-[20vh] overflow-y-auto">
          <MemoryPanel />
        </div>
      </section>
    </div>

    <!-- 底部状态 / 用户 -->
    <div class="flex items-center gap-2 border-t border-line px-3 py-2.5">
      <span :class="['h-[7px] w-[7px] flex-shrink-0 rounded-full', healthOk ? 'bg-ok' : 'bg-warn']" />
      <span class="min-w-0 flex-1 truncate text-[11px] text-ink-faint">{{ healthText }}</span>
      <button
        class="grid h-6 w-6 flex-shrink-0 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
        :title="theme.mode === 'dark' ? '切换到亮色主题' : '切换到暗色主题'"
        @click="theme.toggle()"
      >
        <Icon :name="theme.mode === 'dark' ? 'sun' : 'moon'" :size="13" />
      </button>
      <button
        v-if="!auth.user"
        class="rounded-md border border-line-2 px-2 py-1 text-[11.5px] text-ink-dim transition hover:border-accent/50 hover:text-ink"
        @click="auth.openAuth('login')"
      >
        登录
      </button>
      <UserMenu v-else @profile="emit('profile')" />
    </div>

    <!-- 拖拽调整宽度手柄（悬停变双箭头） -->
    <div
      class="absolute -right-[3px] top-0 z-20 h-full w-[7px] cursor-col-resize transition-colors hover:bg-accent/25"
      title="拖拽调整宽度"
      @mousedown="startResize"
    ></div>
  </aside>
</template>

