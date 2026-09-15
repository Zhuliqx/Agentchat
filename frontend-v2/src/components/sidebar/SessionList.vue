<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { useSessionsStore } from "@/stores/sessions";
import { useChatStore } from "@/stores/chat";
import { useDialogStore } from "@/stores/dialog";
import Icon from "@/components/common/Icon.vue";
import ActionSheet, { type ActionSheetItem } from "@/components/common/ActionSheet.vue";
import { dayBucket, relativeTime } from "@/utils/time";
import { useNow } from "@/composables/useNow";
import { useOpenSession } from "@/composables/useOpenSession";
import type { Session } from "@/types/api";

const sessions = useSessionsStore();
const chat = useChatStore();
const ui = useDialogStore();

// 相对时间每分钟刷新一次：同名会话靠"最后更新时间"区分
const now = useNow();
const openSessionOrSkip = useOpenSession();
/** 长按弹过操作表后，抬手那一下的 click 要吞掉（否则会顺带打开会话） */
let swallowClick = false;

/**
 * 分组展示：置顶单独一组（它跟时间无关，混进"更早"会让置顶失效），
 * 其余按最后更新时间的自然日分 今天 / 昨天 / 更早；空组不渲染。
 */
const groups = computed(() => {
  const buckets: Record<string, Session[]> = {
    pinned: [],
    today: [],
    yesterday: [],
    earlier: [],
  };
  for (const s of sessions.list) {
    buckets[s.pinned ? "pinned" : dayBucket(s.updated_at, now.value)].push(s);
  }
  return [
    { key: "pinned", label: "置顶", items: buckets.pinned },
    { key: "today", label: "今天", items: buckets.today },
    { key: "yesterday", label: "昨天", items: buckets.yesterday },
    { key: "earlier", label: "更早", items: buckets.earlier },
  ].filter((g) => g.items.length);
});

function openSession(id: string) {
  if (sessions.batchMode) {
    sessions.toggleSelect(id);
    return;
  }
  // 长按刚弹过菜单，抬手那一下的 click 不该再打开会话
  if (swallowClick) {
    swallowClick = false;
    return;
  }
  openSessionOrSkip(id);
}

function rename(id: string, title: string) {
  sessions.rename(id, title);
}

async function remove(id: string) {
  if (!(await ui.confirm("确定删除该会话？"))) return;
  const wasCurrent = sessions.currentId === id;
  await sessions.remove(id);
  if (wasCurrent) {
    chat.clear();
    if (sessions.currentId) chat.loadHistory(sessions.currentId);
  }
}

function onDblClickTitle(e: MouseEvent, id: string) {
  beginInlineRename(e.target as HTMLElement, id);
}

/** 标题元素按会话 id 记住：操作表里的"重命名"要找回对应那一行的标题 */
const titleEls = new Map<string, HTMLElement>();
function bindTitleEl(id: string, el: unknown) {
  if (el instanceof HTMLElement) titleEls.set(id, el);
  else titleEls.delete(id);
}

/** 就地重命名：把标题 span 换成输入框，Enter 保存 / Esc 放弃 / 失焦按保存处理 */
function beginInlineRename(target: HTMLElement, id: string) {
  const old = target.textContent || "";
  const input = document.createElement("input");
  input.className =
    "w-full rounded border border-accent bg-surface px-1 text-xs text-ink outline-none";
  input.value = old;
  target.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const restore = (t: string) => {
    const span = document.createElement("span");
    span.className = "truncate";
    span.textContent = t;
    input.replaceWith(span);
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") finish(true);
    else if (ev.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
  function finish(save: boolean) {
    if (done) return;
    done = true;
    const v = input.value.trim();
    if (save && v && v !== old) rename(id, v);
    else restore(v || old);
  }
}

// ---- 触屏长按：hover 在触屏上唤不出操作按钮，用长按弹操作表兜底 ----

const LONG_PRESS_MS = 450;
const sheetFor = ref<Session | null>(null);
let pressTimer = 0;

const sheetItems = computed<ActionSheetItem[]>(() => [
  {
    key: "pin",
    label: sheetFor.value?.pinned ? "取消置顶" : "置顶",
    icon: sheetFor.value?.pinned ? "bookmark" : "pin",
  },
  { key: "rename", label: "重命名", icon: "edit" },
  { key: "delete", label: "删除", icon: "trash", danger: true },
]);

function cancelLongPress() {
  if (!pressTimer) return;
  clearTimeout(pressTimer);
  pressTimer = 0;
}

function onRowPointerDown(e: PointerEvent, s: Session) {
  if (sessions.batchMode || e.pointerType === "mouse") return;
  cancelLongPress();
  pressTimer = window.setTimeout(() => {
    pressTimer = 0;
    swallowClick = true;
    sheetFor.value = s;
  }, LONG_PRESS_MS);
}

async function onSheetSelect(key: string) {
  const s = sheetFor.value;
  sheetFor.value = null;
  if (!s) return;
  if (key === "pin") {
    sessions.pin(s.id, !s.pinned);
    return;
  }
  if (key === "rename") {
    // 重命名复用列表里的就地编辑框（长按后行仍在原位，按 id 找回标题元素）
    const el = titleEls.get(s.id);
    if (el) beginInlineRename(el, s.id);
    return;
  }
  if (key === "delete") await remove(s.id);
}

onBeforeUnmount(cancelLongPress);
</script>

<template>
  <div class="flex flex-col gap-px">
    <!-- 首次加载：先给骨架，避免"空列表 → 突然出现"的突兀感 -->
    <template v-if="sessions.loading && !sessions.list.length">
      <span
        v-for="i in 5"
        :key="i"
        class="mx-1.5 mb-0.5 block h-6 animate-pulse rounded bg-surface-3/70"
      />
      <span class="sr-only">正在加载会话…</span>
    </template>
    <template v-else>
      <template v-for="g in groups" :key="g.key">
        <div
          data-session-group
          class="px-2.5 pb-0.5 pt-2 text-2xs font-medium tracking-wide text-ink-faint"
        >
          {{ g.label }}
        </div>
        <button
          v-for="s in g.items"
          :key="s.id"
          class="group relative flex items-center gap-2 rounded-md py-[6px] pl-3 pr-1.5 text-left text-xs transition coarse:min-h-10"
          :class="
            !sessions.batchMode && s.id === sessions.currentId
              ? 'bg-accent/12 text-ink'
              : 'text-ink-dim hover:bg-surface-2 hover:text-ink'
          "
          @click="openSession(s.id)"
          @pointerdown="(e: PointerEvent) => onRowPointerDown(e, s)"
          @pointerup="cancelLongPress"
          @pointerleave="cancelLongPress"
          @pointercancel="cancelLongPress"
          @contextmenu.prevent
        >
          <span
            v-if="!sessions.batchMode && s.id === sessions.currentId"
            class="absolute left-0 top-1/2 h-3.5 w-[2px] -translate-y-1/2 rounded-full bg-accent"
          />
          <input
            v-if="sessions.batchMode"
            type="checkbox"
            class="accent-accent h-3 w-3"
            :checked="sessions.selected.has(s.id)"
            @click.stop
            @change="sessions.toggleSelect(s.id)"
          />
          <Icon v-if="s.pinned" name="bookmark" :size="11" class="flex-shrink-0 text-accent" />
          <span
            :ref="(el) => bindTitleEl(s.id, el)"
            class="min-w-0 flex-1 truncate"
            :title="s.title"
            @dblclick="(e: MouseEvent) => onDblClickTitle(e, s.id)"
          >
            {{ s.title }}
          </span>
          <template v-if="!sessions.batchMode">
            <!-- 默认显示相对时间，悬停让位给置顶/删除按钮；触屏没有 hover，直接常显 -->
            <span
              class="flex-shrink-0 text-2xs tabular-nums text-ink-faint group-hover:hidden coarse:hidden"
            >
              {{ relativeTime(s.updated_at, now) }}
            </span>
            <span class="hidden flex-shrink-0 items-center gap-1 group-hover:flex coarse:flex">
              <button
                class="grid h-5 w-5 place-items-center rounded text-ink-faint transition hover:bg-surface-3 hover:text-accent coarse:h-8 coarse:w-8"
                :title="s.pinned ? '取消置顶' : '置顶'"
                @click.stop="sessions.pin(s.id, !s.pinned)"
              >
                <Icon :name="s.pinned ? 'bookmark' : 'pin'" :size="13" />
              </button>
              <button
                class="grid h-5 w-5 place-items-center rounded text-ink-faint transition hover:bg-err/10 hover:text-err coarse:h-8 coarse:w-8"
                title="删除会话"
                @click.stop="remove(s.id)"
              >
                <Icon name="trash" :size="13" />
              </button>
            </span>
          </template>
        </button>
      </template>
    </template>

    <ActionSheet
      :open="!!sheetFor"
      :title="sheetFor?.title"
      :items="sheetItems"
      @select="onSheetSelect"
      @close="sheetFor = null"
    />
  </div>
</template>
