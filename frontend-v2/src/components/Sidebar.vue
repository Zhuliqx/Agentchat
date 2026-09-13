<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useElementSize } from "@vueuse/core";
import { searchApi } from "@/api";
import { usePointerDrag } from "@/composables/usePointerDrag";
import { useSessionsStore } from "@/stores/sessions";
import { useAuthStore } from "@/stores/auth";
import { useDocsStore } from "@/stores/docs";
import { useMemoryStore } from "@/stores/memory";
import { useThemeStore } from "@/stores/theme";
import { useChatStore } from "@/stores/chat";
import SessionList from "./sidebar/SessionList.vue";
import DocPanel from "./sidebar/DocPanel.vue";
import MemoryPanel from "./sidebar/MemoryPanel.vue";
import UserMenu from "./sidebar/UserMenu.vue";
import Icon from "@/components/common/Icon.vue";
import Tooltip from "@/components/common/Tooltip.vue";
import { SIDEBAR_DEFAULT_WIDTH, SIDEBAR_MIN_WIDTH } from "@/utils/sidebarLayout";

const props = defineProps<{
  healthText: string;
  healthOk: boolean;
  /** 当前渲染宽度（折叠时为 0） */
  width?: number;
  /** 内容层保持的宽度：折叠动画期间不再逐像素挤压内容 */
  contentWidth?: number;
  /** 展开状态，用于内容淡入淡出 */
  open?: boolean;
  /** 窄屏抽屉形态：固定定位悬浮在会话区之上，不参与布局挤压，也不提供拖拽调宽 */
  overlay?: boolean;
}>();
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
  if (id !== sessions.currentId) {
    sessions.currentId = id;
    chat.loadHistory(id);
  }
  searchText.value = "";
  searchResults.value = null;
}

const folded = ref<Record<string, boolean>>({
  sessions: localStorage.getItem("fold-sessions") === "1",
  docs: localStorage.getItem("fold-docs") === "1",
  memory: localStorage.getItem("fold-memory") === "1",
});
function toggle(key: string) {
  folded.value[key] = !folded.value[key];
  localStorage.setItem(`fold-${key}`, folded.value[key] ? "1" : "0");
}

// 底部面板（文档/记忆）折叠期间容器高度在动画：此时会话列表的 max-height 要立刻跟随实测高度，
// 否则它会再用 300ms 缓动追一次，观感是"文档栏收完了，会话列表才慢一拍地长出来"
const panelsAnimating = ref(false);
let panelsAnimatingTimer: number | undefined;
watch(
  () => [folded.value.docs, folded.value.memory],
  () => {
    panelsAnimating.value = true;
    clearTimeout(panelsAnimatingTimer);
    panelsAnimatingTimer = window.setTimeout(() => (panelsAnimating.value = false), 340);
  },
);
onBeforeUnmount(() => clearTimeout(panelsAnimatingTimer));

async function newSession() {
  // 当前会话还是空的（刚建完还没发消息）：复用而不是再建一个，
  // 否则连点"新建会话"会堆出一串空会话
  if (sessions.currentId && !chat.messages.length) return;
  await sessions.create();
  // 切换到新会话：清空聊天区（避免仍显示旧会话消息）
  chat.clear();
}

// ---- 拖拽调整宽度（收窄到最小宽度自动折叠；折叠后不松手反向拖拽自动展开） ----
// 拖拽期间禁用过渡保证跟手；越过阈值折叠时改用过渡动画，避免"啪"地消失
const widthDragging = ref(false);
let widthStartW = SIDEBAR_DEFAULT_WIDTH;
let widthCollapsed = false; // 本次拖拽中是否已触发折叠

const startResize = usePointerDrag({
  onStart: () => {
    // 折叠态从最小宽度起步，并复用"反向拖回自动展开"分支：
    // 这样在左边缘往右拖一点点就能把侧栏拉出来，不用先拖过 180px 死区
    const collapsed = props.open === false;
    widthStartW = collapsed ? SIDEBAR_MIN_WIDTH : (props.width ?? SIDEBAR_DEFAULT_WIDTH);
    widthCollapsed = collapsed;
    widthDragging.value = true;
  },
  onMove: (e, ctx) => {
    const raw = widthStartW + e.clientX - ctx.startX;
    if (widthCollapsed) {
      // 已折叠：向右拖回阈值及以上 → 自动展开并继续调整宽度
      if (raw >= SIDEBAR_MIN_WIDTH) {
        widthCollapsed = false;
        widthDragging.value = true;
        emit("toggle");
        emit("width-change", raw);
      }
      return;
    }
    // 收窄到阈值以下：把宽度交给过渡动画收拢到 0（保持拖拽监听，等待反向拖回）
    if (raw < SIDEBAR_MIN_WIDTH) {
      widthCollapsed = true;
      widthDragging.value = false;
      emit("toggle");
      return;
    }
    emit("width-change", Math.max(SIDEBAR_MIN_WIDTH, raw));
  },
  onEnd: () => {
    widthDragging.value = false;
  },
});

// ---- 文档 / 记忆面板高度：各自独立控制 ----
// 上限由实测的 aside 高度算出；渲染时再按可用空间收窄，
// 所以窗口变矮或把某个面板拖得过大都不会溢出。
const asideRef = ref<HTMLElement | null>(null);
const { height: asideHeight } = useElementSize(asideRef);
// 会话列表折叠时用它所在区域的实测高度做动画区间：max-height 从 100vh 收起会让
// 变化量远大于列表实际高度，观感是"先不动、最后一下收掉"
const sessionsAreaRef = ref<HTMLElement | null>(null);
const { height: sessionsAreaH } = useElementSize(sessionsAreaRef);

const PANEL_HEIGHTS_KEY = "sidebar-panel-heights";
const PANEL_HEADER = 40; // 每个卡片的标题栏（拖拽条悬浮在边框上，不占高度）
const BOTTOM_CHROME = 14; // 底部容器上下内边距 + 两个卡片间距
const BOTTOM_RESERVED = 268; // 品牌区 + 新建/搜索 + 会话区最小高度 + 状态栏
/** 面板内容区的最小高度：放下各自固定控件后还留得下一行列表 */
const CONTENT_MIN = { doc: 137, mem: 85 };
/** 默认高度与旧版一致（18vh / 14vh） */
const PANEL_DEFAULT_RATIO = { doc: 0.18, mem: 0.14 };

function defaultContent(which: "doc" | "mem") {
  return Math.round(window.innerHeight * PANEL_DEFAULT_RATIO[which]);
}

function loadHeights() {
  const fallback = { doc: defaultContent("doc"), mem: defaultContent("mem") };
  try {
    const raw = localStorage.getItem(PANEL_HEIGHTS_KEY);
    if (raw) {
      const p = JSON.parse(raw) as { doc?: unknown; mem?: unknown };
      return {
        doc: Number(p.doc) > 0 ? Number(p.doc) : fallback.doc,
        mem: Number(p.mem) > 0 ? Number(p.mem) : fallback.mem,
      };
    }
  } catch {
    /* 脏数据回落到默认值 */
  }
  return fallback;
}

const initialHeights = loadHeights();
const docContentH = ref(initialHeights.doc);
const memContentH = ref(initialHeights.mem);
// 拖拽调高期间关闭折叠过渡，否则高度变化会滞后于指针
const panelDragging = ref(false);

/** 两个面板内容区合计可用的高度（扣掉顶部固定区、状态栏与两个卡片标题栏） */
const contentBudget = computed(() => {
  // 拖拽条是贴在卡片边框上的悬浮层，卡片高度 = 标题栏 + 内容区
  const chrome = BOTTOM_CHROME + 2 * PANEL_HEADER;
  const minSum =
    (folded.value.docs ? 0 : CONTENT_MIN.doc) + (folded.value.memory ? 0 : CONTENT_MIN.mem);
  return Math.max(minSum, asideHeight.value - BOTTOM_RESERVED - chrome);
});

// 渲染高度：窗口变矮导致放不下时按比例收窄，不改写用户存下的值
const renderedHeights = computed(() => {
  const budget = contentBudget.value;
  const doc = folded.value.docs ? 0 : Math.max(CONTENT_MIN.doc, docContentH.value);
  const mem = folded.value.memory ? 0 : Math.max(CONTENT_MIN.mem, memContentH.value);
  const total = doc + mem;
  if (total <= budget || total === 0) return { doc, mem };
  const scale = budget / total;
  const fit = (value: number, min: number) =>
    value === 0 ? 0 : Math.max(min, Math.floor(value * scale));
  return { doc: fit(doc, CONTENT_MIN.doc), mem: fit(mem, CONTENT_MIN.mem) };
});

function persistHeights() {
  localStorage.setItem(
    PANEL_HEIGHTS_KEY,
    JSON.stringify({ doc: docContentH.value, mem: memContentH.value }),
  );
}

let docStartH = 0;
const startDocResize = usePointerDrag({
  onStart: () => {
    panelDragging.value = true;
    docStartH = renderedHeights.value.doc;
  },
  onMove: (e, ctx) => {
    // 向上拖 = 文档面板变高（上限是"预算减去记忆面板当前占用"）
    const max = Math.max(CONTENT_MIN.doc, contentBudget.value - renderedHeights.value.mem);
    const next = docStartH + (ctx.startY - e.clientY);
    docContentH.value = Math.min(max, Math.max(CONTENT_MIN.doc, Math.round(next)));
  },
  onEnd: () => {
    panelDragging.value = false;
    persistHeights();
  },
});

let memStartH = 0;
const startMemResize = usePointerDrag({
  onStart: () => {
    panelDragging.value = true;
    memStartH = renderedHeights.value.mem;
  },
  onMove: (e, ctx) => {
    // 向上拖 = 记忆面板变高
    const max = Math.max(CONTENT_MIN.mem, contentBudget.value - renderedHeights.value.doc);
    const next = memStartH + (ctx.startY - e.clientY);
    memContentH.value = Math.min(max, Math.max(CONTENT_MIN.mem, Math.round(next)));
  },
  onEnd: () => {
    panelDragging.value = false;
    persistHeights();
  },
});

function resetPanelH(which: "doc" | "mem") {
  const target = which === "doc" ? docContentH : memContentH;
  target.value = defaultContent(which);
  persistHeights();
}
</script>

<template>
  <aside
    ref="asideRef"
    class="sb-shell flex flex-col overflow-hidden border-r border-line bg-surface"
    :class="[
      overlay ? 'fixed inset-y-0 left-0 z-40 shadow-[0_0_40px_rgba(0,0,0,0.45)]' : 'relative',
      widthDragging ? 'sb-shell--dragging' : '',
    ]"
    :style="{ width: (width ?? 0) + 'px', minWidth: (width ?? 0) + 'px' }"
  >
    <!-- 内容层保持展开宽度：折叠时整体被裁切 + 淡出，不会逐像素挤压换行 -->
    <div
      class="sb-inner flex h-full flex-col"
      :class="open === false ? 'opacity-0' : 'opacity-100'"
      :style="{ width: (contentWidth ?? width ?? 0) + 'px' }"
    >
      <!-- 品牌 + 收起 -->
      <div class="flex items-center gap-2.5 px-4 pt-4 pb-3">
        <div
          class="grid h-8 w-8 flex-shrink-0 place-items-center rounded-[9px] bg-accent/15 text-accent"
        >
          <Icon name="agents" :size="16" />
        </div>
        <div class="min-w-0">
          <h1 class="text-sm font-semibold leading-tight tracking-tight">Multi-Agent</h1>
          <span class="block truncate text-2xs text-ink-faint">RAG · MCP · LangGraph</span>
        </div>
        <Tooltip label="收起侧边栏" placement="bottom">
          <button
            class="ml-auto grid h-6 w-6 flex-shrink-0 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
            aria-label="收起侧边栏"
            @click="emit('toggle')"
          >
            <Icon name="chevron" :size="14" class="rotate-90" />
          </button>
        </Tooltip>
      </div>

      <!-- 新建会话 -->
      <div class="px-3 pb-2">
        <button
          class="flex w-full items-center gap-2 rounded-lg border border-line-2 bg-surface-2 px-3 py-[7px] text-xs font-medium text-ink transition hover:border-accent/50 hover:bg-surface-3"
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
            class="h-8 w-full rounded-lg border border-line-2 bg-surface-2 pl-8 pr-2.5 text-xs text-ink outline-none transition placeholder:text-ink-faint focus:border-accent focus:ring-2 focus:ring-accent/15"
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
          <div v-if="searching" class="px-2 py-2 text-2xs text-ink-faint">搜索中…</div>
          <template v-else-if="searchResults">
            <div
              v-if="searchResults.sessions.length"
              class="px-1.5 pb-1 pt-1 text-2xs font-medium uppercase tracking-wide text-ink-faint"
            >
              会话
            </div>
            <button
              v-for="s in searchResults.sessions"
              :key="'s' + s.id"
              class="flex w-full items-center gap-1.5 rounded px-1.5 py-1.5 text-left text-xs text-ink-dim transition hover:bg-surface-3 hover:text-ink"
              @click="gotoSession(s.id)"
            >
              <Icon name="chat" :size="12" class="flex-shrink-0 text-ink-faint" />
              <span class="truncate">{{ s.title }}</span>
            </button>
            <div
              v-if="searchResults.messages.length"
              class="px-1.5 py-1 text-2xs font-medium uppercase tracking-wide text-ink-faint"
            >
              消息
            </div>
            <button
              v-for="(m, i) in searchResults.messages"
              :key="'m' + i"
              class="flex w-full flex-col gap-0.5 rounded px-1.5 py-1.5 text-left transition hover:bg-surface-3"
              @click="gotoSession(m.session_id)"
            >
              <span class="flex items-center gap-1.5 text-2xs text-ink-dim">
                <span
                  class="rounded bg-surface-3 px-1 py-px text-2xs"
                  :class="m.role === 'user' ? 'text-accent' : 'text-orbit'"
                >
                  {{ m.role === "user" ? "我" : "助手" }}
                </span>
                <span class="truncate">{{ m.session_title }}</span>
              </span>
              <span class="line-clamp-1 text-2xs text-ink-faint">{{ m.content }}</span>
            </button>
            <div
              v-if="!searchResults.sessions.length && !searchResults.messages.length"
              class="px-2 py-2 text-2xs text-ink-faint"
            >
              无匹配结果
            </div>
          </template>
        </div>
      </div>

      <!-- 会话区：弹性铺满剩余空间，列表滚动（保留最小一行可见，避免被底部面板挤没/重叠） -->
      <div class="no-scrollbar flex min-h-[56px] flex-1 flex-col px-2.5 pb-1">
        <section ref="sessionsAreaRef" class="mb-1 flex min-h-0 flex-1 flex-col">
          <div class="flex items-center gap-1.5 px-1.5 py-1.5">
            <button
              class="flex items-center gap-1 text-2xs font-medium uppercase tracking-[0.08em] text-ink-faint transition hover:text-ink-dim"
              @click="toggle('sessions')"
            >
              <Icon
                name="chevron"
                :size="11"
                class="transition-transform"
                :class="folded.sessions ? '-rotate-90' : ''"
              />
              {{ sessions.batchMode ? "批量选择" : "会话" }}
              <span
                class="ml-0.5 rounded bg-surface-3 px-1 py-px text-2xs font-normal text-ink-dim"
                >{{ sessions.list.length }}</span
              >
            </button>
            <Tooltip
              class="ml-auto"
              :label="sessions.batchMode ? '完成' : '多选'"
              placement="bottom"
            >
              <button
                class="text-ink-faint transition hover:text-ink-dim"
                :aria-label="sessions.batchMode ? '完成' : '多选'"
                @click="sessions.toggleBatch()"
              >
                <Icon v-if="sessions.batchMode" name="check" :size="13" />
                <Icon v-else name="dots" :size="13" />
              </button>
            </Tooltip>
          </div>
          <div v-if="sessions.batchMode" class="mb-1 flex gap-1 px-1.5">
            <button
              class="rounded border border-line-2 px-1.5 py-0.5 text-2xs text-ink-dim hover:text-ink"
              @click="sessions.toggleSelectAll()"
            >
              全选
            </button>
            <button
              class="rounded border border-err/40 px-1.5 py-0.5 text-2xs text-err disabled:opacity-40"
              :disabled="!sessions.selected.size"
              @click="sessions.batchDelete([...sessions.selected])"
            >
              删除{{ sessions.selected.size ? ` (${sessions.selected.size})` : "" }}
            </button>
          </div>
          <div
            class="sb-panel no-scrollbar min-h-0 flex-1 overflow-y-auto"
            :class="panelsAnimating ? 'sb-panel--follow' : ''"
            :style="{
              maxHeight: folded.sessions ? '0px' : sessionsAreaH ? sessionsAreaH + 'px' : '100vh',
            }"
          >
            <SessionList />
          </div>
        </section>
      </div>

      <!-- 文档 + 记忆：各自独立卡片，各自的拖拽条只控制自己 -->
      <div class="flex flex-shrink-0 flex-col gap-1.5 border-t border-line px-2.5 pt-1 pb-1">
        <!-- 知识库文档 -->
        <div class="relative">
          <!-- 拖拽条紧贴卡片上边框（交接线）：命中区只在线上方 5px——
               既不越界到上方内容，也不压住卡片标题（折叠按钮）；悬停时线上亮一条细线 -->
          <div
            v-if="!folded.docs"
            class="group absolute inset-x-0 -top-[5px] z-10 h-[5px] cursor-row-resize touch-none select-none"
            title="拖拽调整文档面板高度 · 双击恢复默认"
            @pointerdown="startDocResize"
            @dblclick="resetPanelH('doc')"
          >
            <span
              class="pointer-events-none absolute inset-x-0 bottom-0 h-[2px] bg-accent opacity-0 transition-opacity duration-150 group-hover:opacity-100"
            />
          </div>
          <section class="overflow-hidden rounded-lg border border-line">
            <div class="flex items-center">
              <button
                class="flex min-w-0 flex-1 items-center gap-1 px-1.5 py-1.5 text-left text-2xs font-medium uppercase tracking-[0.08em] text-ink-faint transition hover:text-ink-dim"
                @click="toggle('docs')"
              >
                <Icon
                  name="chevron"
                  :size="11"
                  class="flex-shrink-0 transition-transform"
                  :class="folded.docs ? '-rotate-90' : ''"
                />
                <span class="min-w-0 flex-1 truncate">文档</span>
                <span
                  class="ml-0.5 rounded bg-surface-3 px-1 py-px text-2xs font-normal text-ink-dim"
                  >{{ docsCount }}</span
                >
              </button>
            </div>
            <div
              class="sb-panel flex min-h-0 flex-col overflow-hidden"
              :class="panelDragging ? 'sb-panel--dragging' : ''"
              :style="{ height: folded.docs ? '0px' : renderedHeights.doc + 'px' }"
            >
              <DocPanel />
            </div>
          </section>
        </div>

        <!-- 长期记忆 -->
        <div class="relative">
          <!-- 同上：命中区贴住记忆卡片的上边框，不与内容/标题抢点击 -->
          <div
            v-if="!folded.memory"
            class="group absolute inset-x-0 -top-[5px] z-10 h-[5px] cursor-row-resize touch-none select-none"
            title="拖拽调整记忆面板高度 · 双击恢复默认"
            @pointerdown="startMemResize"
            @dblclick="resetPanelH('mem')"
          >
            <span
              class="pointer-events-none absolute inset-x-0 bottom-0 h-[2px] bg-accent opacity-0 transition-opacity duration-150 group-hover:opacity-100"
            />
          </div>
          <section class="overflow-hidden rounded-lg border border-line">
            <div class="flex items-center">
              <button
                class="flex min-w-0 flex-1 items-center gap-1 px-1.5 py-1.5 text-left text-2xs font-medium uppercase tracking-[0.08em] text-ink-faint transition hover:text-ink-dim"
                @click="toggle('memory')"
              >
                <Icon
                  name="chevron"
                  :size="11"
                  class="flex-shrink-0 transition-transform"
                  :class="folded.memory ? '-rotate-90' : ''"
                />
                <span class="min-w-0 flex-1 truncate">记忆</span>
                <span
                  class="ml-0.5 rounded bg-surface-3 px-1 py-px text-2xs font-normal text-ink-dim"
                  >{{ memoryCount }}</span
                >
              </button>
            </div>
            <div
              class="sb-panel flex min-h-0 flex-col overflow-hidden"
              :class="panelDragging ? 'sb-panel--dragging' : ''"
              :style="{ height: folded.memory ? '0px' : renderedHeights.mem + 'px' }"
            >
              <MemoryPanel />
            </div>
          </section>
        </div>
      </div>

      <!-- 底部状态 / 用户 -->
      <div class="flex items-center gap-2 border-t border-line px-3 py-2.5">
        <span
          :class="['h-[7px] w-[7px] flex-shrink-0 rounded-full', healthOk ? 'bg-ok' : 'bg-warn']"
        />
        <span class="min-w-0 flex-1 truncate text-2xs text-ink-faint">{{ healthText }}</span>
        <Tooltip :label="theme.mode === 'dark' ? '切换到亮色主题' : '切换到暗色主题'">
          <button
            class="grid h-6 w-6 flex-shrink-0 place-items-center rounded-md text-ink-faint transition hover:bg-surface-2 hover:text-ink"
            :aria-label="theme.mode === 'dark' ? '切换到亮色主题' : '切换到暗色主题'"
            @click="theme.toggle()"
          >
            <Icon :name="theme.mode === 'dark' ? 'sun' : 'moon'" :size="13" />
          </button>
        </Tooltip>
        <button
          v-if="!auth.user"
          class="rounded-md border border-line-2 px-2 py-1 text-2xs text-ink-dim transition hover:border-accent/50 hover:text-ink"
          @click="auth.openAuth('login')"
        >
          登录
        </button>
        <UserMenu v-else @profile="emit('profile')" @admin="emit('admin')" />
      </div>
    </div>

    <!-- 拖拽调整宽度手柄：
         fixed 定位（aside 有 overflow-hidden，绝对定位会被裁掉）；
         命中区紧贴"内容区 / 侧栏"的交接线——展开态落在侧栏内最后一列、不覆盖消息区，
         折叠态贴在屏幕左边缘；悬停时只在交接线上亮一条细线，所见即所拖 -->
    <div
      v-if="!overlay"
      class="group fixed top-0 z-30 h-full w-[5px] cursor-col-resize touch-none"
      :style="{ left: (open === false ? 0 : Math.max(0, (width ?? 0) - 5)) + 'px' }"
      title="拖拽调整宽度"
      @pointerdown="startResize"
    >
      <span
        class="pointer-events-none absolute top-0 h-full w-[2px] bg-accent opacity-0 transition-opacity duration-150 group-hover:opacity-100"
        :class="open === false ? 'left-0' : 'right-0'"
      ></span>
    </div>
  </aside>
</template>
