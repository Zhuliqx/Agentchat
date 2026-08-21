<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { searchApi } from "@/api";
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
  admin: [];
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

// ---- 全局搜索（会话标题 + 消息内容，防抖 300ms） ----
const searchText = ref("");
const searching = ref(false);
const searchResults = ref<{
  sessions: { id: string; title: string; pinned?: boolean }[];
  messages: { session_id: string; session_title: string; role: string; content: string }[];
} | null>(null);
let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(searchText, (v) => {
  if (searchTimer) clearTimeout(searchTimer);
  const q = v.trim();
  if (!q) {
    searchResults.value = null;
    searching.value = false;
    return;
  }
  searching.value = true;
  searchTimer = setTimeout(async () => {
    try {
      searchResults.value = await searchApi.search(q);
    } catch {
      searchResults.value = null;
    } finally {
      searching.value = false;
    }
  }, 300);
});
function gotoSession(id: string) {
  sessions.currentId = id;
  chat.loadHistory(id);
  searchText.value = "";
  searchResults.value = null;
}

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
// 拖拽宽度时禁用宽度过渡（否则 transition 使宽度变化滞后，显得不跟手）
const widthDragging = ref(false);
function startResize(e: MouseEvent) {
  e.preventDefault();
  widthDragging.value = true;
  const startX = e.clientX;
  const startW = props.width ?? 236;
  let collapsed = false; // 本次拖拽中是否已触发折叠
  let done = false;
  const end = () => {
    if (done) return;
    done = true;
    widthDragging.value = false;
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

// ---- 文档 / 记忆面板高度拖拽（各自面板标题栏上的拖拽条，双击恢复默认） ----
function loadPanelH(key: string): number | null {
  const v = parseInt(localStorage.getItem(key) ?? "", 10);
  return Number.isFinite(v) && v > 0 ? v : null;
}
const docPanelH = ref<number | null>(loadPanelH("panel-docs-h"));
const memPanelH = ref<number | null>(loadPanelH("panel-mem-h"));
// 拖拽面板高度时禁用高度过渡（否则 transition 使高度变化滞后，显得不跟手）
const panelDragging = ref(false);
const PANEL_META = {
  doc: { h: docPanelH, key: "panel-docs-h", fallback: 0.18 }, // 与默认 18vh 一致
  mem: { h: memPanelH, key: "panel-mem-h", fallback: 0.14 }, // 与默认 14vh 一致
} as const;
let panelResizeStartY = 0;
let panelResizeStartH = 0;
function startPanelResize(which: "doc" | "mem", e: MouseEvent) {
  e.preventDefault();
  panelDragging.value = true;
  const meta = PANEL_META[which];
  const aside = document.querySelector("aside");
  const asideH = aside ? aside.clientHeight : window.innerHeight;
  const asideTop = aside ? aside.getBoundingClientRect().top : 0;

  // ---- 彻底方案：拖拽时实际测量固定占用，不依赖估算 ----
  // 顶部固定区高度 = 会话区容器顶部相对 aside 顶部的距离
  let topH = 142;
  if (aside) {
    const sessionWrap = Array.from(aside.children).find(
      (el) =>
        (el.className || "").includes("flex-1") &&
        (el.className || "").includes("flex-col") &&
        (el.className || "").includes("px-2.5"),
    );
    if (sessionWrap) {
      topH = sessionWrap.getBoundingClientRect().top - asideTop;
    }
  }
  // 底部状态栏高度（实测）
  let statusH = 46;
  if (aside) {
    const statusEl = Array.from(aside.querySelectorAll("div")).find(
      (d) =>
        (d.textContent || "").includes("MCP") && (d.className || "").includes("border-t"),
    );
    if (statusEl) statusH = statusEl.offsetHeight;
  }
  // 状态栏理想顶部位置 = aside 高 - 状态栏高（固定，保证状态栏底部正好在界面内，
  // 不受当前面板高度影响——不能用测量当前位置，否则"越推越溢出"）
  const statusTopOffset = asideH - statusH;

  // 会话区最小保留高度 + 单个面板的标题栏/拖拽条高度
  const SESSION_MIN = 56;
  const PANEL_HEADER = 44;
  // 底部容器额外占用：pt/pb + 面板间 gap + 卡片边框（≈16）
  const EXTRA = 16;
  // 另一面板当前总高（内容 + 面板头），fallback 与默认高度一致
  const otherH =
    which === "doc"
      ? (memPanelH.value ?? Math.round(window.innerHeight * 0.14)) + PANEL_HEADER
      : (docPanelH.value ?? Math.round(window.innerHeight * 0.18)) + PANEL_HEADER;
  // 最小高度：能容纳面板头部内容（文档=上传区，记忆=搜索/添加区）
  const MIN = which === "doc" ? 88 : 96;
  // 当前面板内容区可用高度 = 状态栏顶部 - 顶部固定 - 会话最小 - 底部额外 - 另一面板总高 - 当前面板头
  const maxH = Math.max(
    MIN,
    statusTopOffset - topH - SESSION_MIN - EXTRA - otherH - PANEL_HEADER,
  );

  panelResizeStartY = e.clientY;
  panelResizeStartH = meta.h.value ?? Math.round(window.innerHeight * meta.fallback);
  const onMove = (ev: MouseEvent) => {
    const h = Math.round(
      Math.min(
        Math.max(panelResizeStartH + (panelResizeStartY - ev.clientY), MIN),
        maxH,
      ),
    );
    meta.h.value = h;
    localStorage.setItem(meta.key, String(h));
  };
  const onUp = () => {
    panelDragging.value = false;
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
  };
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}
function resetPanelH(which: "doc" | "mem") {
  const meta = PANEL_META[which];
  meta.h.value = null;
  localStorage.removeItem(meta.key);
}
</script>

<template>
  <aside
    class="relative flex flex-col overflow-hidden border-r border-line bg-surface"
    :class="widthDragging ? 'transition-none' : 'transition-[width,min-width] duration-300 ease-in-out'"
    :style="{ width: (width ?? 0) + 'px', minWidth: (width ?? 0) + 'px' }"
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

    <!-- 全局搜索 -->
    <div class="px-3 pb-2">
      <div class="relative">
        <input
          v-model="searchText"
          type="text"
          class="h-8 w-full rounded-lg border border-line-2 bg-surface-2 pl-8 pr-2.5 text-[12px] text-ink outline-none transition placeholder:text-ink-faint/70 focus:border-accent focus:ring-2 focus:ring-accent/15"
          placeholder="搜索会话与消息…"
        />
        <Icon
          name="search"
          :size="13"
          class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
        />
      </div>
      <!-- 搜索结果 -->
      <div
        v-if="searchText.trim()"
        class="no-scrollbar mt-1.5 max-h-[38vh] overflow-y-auto rounded-lg border border-line bg-surface-2 p-1"
      >
        <div v-if="searching" class="px-2 py-2 text-[11.5px] text-ink-faint">搜索中…</div>
        <template v-else-if="searchResults">
          <div
            v-if="searchResults.sessions.length"
            class="px-1.5 pb-1 pt-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint"
          >
            会话
          </div>
          <button
            v-for="s in searchResults.sessions"
            :key="'s' + s.id"
            class="flex w-full items-center gap-1.5 rounded px-1.5 py-1.5 text-left text-[12px] text-ink-dim transition hover:bg-surface-3 hover:text-ink"
            @click="gotoSession(s.id)"
          >
            <Icon name="chat" :size="12" class="flex-shrink-0 text-ink-faint" />
            <span class="truncate">{{ s.title }}</span>
          </button>
          <div
            v-if="searchResults.messages.length"
            class="px-1.5 py-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint"
          >
            消息
          </div>
          <button
            v-for="(m, i) in searchResults.messages"
            :key="'m' + i"
            class="flex w-full flex-col gap-0.5 rounded px-1.5 py-1.5 text-left transition hover:bg-surface-3"
            @click="gotoSession(m.session_id)"
          >
            <span class="flex items-center gap-1.5 text-[11px] text-ink-dim">
              <span
                class="rounded bg-surface-3 px-1 py-px text-[9.5px]"
                :class="m.role === 'user' ? 'text-accent' : 'text-orbit'"
              >
                {{ m.role === "user" ? "我" : "助手" }}
              </span>
              <span class="truncate">{{ m.session_title }}</span>
            </span>
            <span class="line-clamp-1 text-[11px] text-ink-faint">{{ m.content }}</span>
          </button>
          <div
            v-if="!searchResults.sessions.length && !searchResults.messages.length"
            class="px-2 py-2 text-[11.5px] text-ink-faint"
          >
            无匹配结果
          </div>
        </template>
      </div>
    </div>

    <!-- 会话区：弹性铺满剩余空间，列表滚动（保留最小一行可见，避免被底部面板挤没/重叠） -->
    <div class="no-scrollbar flex min-h-[56px] flex-1 flex-col px-2.5 pb-1">
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
        <div
          class="no-scrollbar min-h-0 flex-1 overflow-y-auto transition-[max-height] duration-300 ease-in-out"
          :style="{ maxHeight: folded.sessions ? '0px' : '100vh' }"
        >
          <SessionList />
        </div>
      </section>
    </div>

    <!-- 文档 + 记忆：固定在底部（各自独立卡片，各自区域内滚动） -->
    <!-- 面板区 flex-shrink-0 保证拖拽跟手；防溢出由拖拽 maxH（实测顶部+理想状态栏位置）与较小默认高度保证 -->
    <div class="flex flex-shrink-0 flex-col gap-1.5 border-t border-line px-2.5 pt-1 pb-1">
      <!-- 知识库文档 -->
      <section class="overflow-hidden rounded-lg border border-line">
        <!-- 文档面板拖拽条（条状）：在标题栏上方，只控制文档面板高度，折叠时不显示 -->
        <div
          v-if="!folded.docs"
          class="group -mx-2.5 flex h-[13px] cursor-row-resize select-none items-center justify-center text-ink-faint/50 transition-colors hover:bg-accent/10 hover:text-ink-dim"
          title="拖拽调整文档面板高度 · 双击恢复默认"
          @mousedown="startPanelResize('doc', $event)"
          @dblclick="resetPanelH('doc')"
        >
          <span class="h-[3px] w-9 rounded-full bg-line-2 transition-colors group-hover:bg-accent/60" />
        </div>
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
        <div
          class="flex min-h-0 flex-col overflow-hidden"
          :class="panelDragging ? 'transition-none' : 'transition-[height] duration-300 ease-in-out'"
          :style="{ height: folded.docs ? '0px' : (docPanelH ? docPanelH + 'px' : '18vh') }"
        >
          <DocPanel />
        </div>
      </section>

      <!-- 长期记忆 -->
      <section class="overflow-hidden rounded-lg border border-line">
        <!-- 记忆面板拖拽条（条状）：在标题栏上方，只控制记忆面板高度，折叠时不显示 -->
        <div
          v-if="!folded.memory"
          class="group -mx-2.5 flex h-[13px] cursor-row-resize select-none items-center justify-center text-ink-faint/50 transition-colors hover:bg-accent/10 hover:text-ink-dim"
          title="拖拽调整记忆面板高度 · 双击恢复默认"
          @mousedown="startPanelResize('mem', $event)"
          @dblclick="resetPanelH('mem')"
        >
          <span class="h-[3px] w-9 rounded-full bg-line-2 transition-colors group-hover:bg-accent/60" />
        </div>
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
        <div
          class="flex min-h-0 flex-col overflow-hidden"
          :class="panelDragging ? 'transition-none' : 'transition-[height] duration-300 ease-in-out'"
          :style="{ height: folded.memory ? '0px' : (memPanelH ? memPanelH + 'px' : '14vh') }"
        >
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
      <UserMenu v-else @profile="emit('profile')" @admin="emit('admin')" />
    </div>

    <!-- 拖拽调整宽度手柄（悬停变双箭头） -->
    <div
      class="absolute -right-[3px] top-0 z-20 h-full w-[7px] cursor-col-resize transition-colors hover:bg-accent/25"
      title="拖拽调整宽度"
      @mousedown="startResize"
    ></div>
  </aside>
</template>