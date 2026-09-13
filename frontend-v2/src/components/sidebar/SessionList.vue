<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { useSessionsStore } from "@/stores/sessions";
import { useChatStore } from "@/stores/chat";
import { useDialogStore } from "@/stores/dialog";
import Icon from "@/components/common/Icon.vue";
import { relativeTime } from "@/utils/time";

const sessions = useSessionsStore();
const chat = useChatStore();
const ui = useDialogStore();

const sorted = computed(() => [...sessions.list]);

// 相对时间每分钟刷新一次：同名会话靠"最后更新时间"区分
const now = ref(Date.now());
const ticker = setInterval(() => (now.value = Date.now()), 60_000);
onBeforeUnmount(() => clearInterval(ticker));

function openSession(id: string) {
  if (sessions.batchMode) {
    sessions.toggleSelect(id);
    return;
  }
  if (id === sessions.currentId) return; // 点击当前会话不应重载并中止在途回答
  sessions.currentId = id;
  chat.loadHistory(id);
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
  const target = e.target as HTMLElement;
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
</script>

<template>
  <div class="flex flex-col gap-px">
    <button
      v-for="s in sorted"
      :key="s.id"
      class="group relative flex items-center gap-2 rounded-md py-[6px] pl-3 pr-1.5 text-left text-xs transition"
      :class="
        !sessions.batchMode && s.id === sessions.currentId
          ? 'bg-accent/12 text-ink'
          : 'text-ink-dim hover:bg-surface-2 hover:text-ink'
      "
      @click="openSession(s.id)"
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
        class="min-w-0 flex-1 truncate"
        :title="s.title"
        @dblclick="(e: MouseEvent) => onDblClickTitle(e, s.id)"
      >
        {{ s.title }}
      </span>
      <template v-if="!sessions.batchMode">
        <!-- 默认显示相对时间，悬停让位给置顶/删除按钮 -->
        <span class="flex-shrink-0 text-2xs tabular-nums text-ink-faint group-hover:hidden">
          {{ relativeTime(s.updated_at, now) }}
        </span>
        <span class="hidden flex-shrink-0 items-center gap-1 group-hover:flex">
          <button
            class="grid h-5 w-5 place-items-center rounded text-ink-faint transition hover:bg-surface-3 hover:text-accent"
            :title="s.pinned ? '取消置顶' : '置顶'"
            @click.stop="sessions.pin(s.id, !s.pinned)"
          >
            <Icon :name="s.pinned ? 'bookmark' : 'pin'" :size="13" />
          </button>
          <button
            class="grid h-5 w-5 place-items-center rounded text-ink-faint transition hover:bg-err/10 hover:text-err"
            title="删除会话"
            @click.stop="remove(s.id)"
          >
            <Icon name="trash" :size="13" />
          </button>
        </span>
      </template>
    </button>
  </div>
</template>
