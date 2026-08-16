<script setup lang="ts">
import { computed } from "vue";
import { useSessionsStore } from "@/stores/sessions";
import { useChatStore } from "@/stores/chat";
import { useAuthStore } from "@/stores/auth";
import Icon from "@/components/common/Icon.vue";

const sessions = useSessionsStore();
const chat = useChatStore();
const auth = useAuthStore();

const sorted = computed(() => [...sessions.list]);

function openSession(id: string) {
  if (sessions.batchMode) {
    sessions.toggleSelect(id);
    return;
  }
  sessions.currentId = id;
  chat.loadHistory(id);
}

function rename(id: string, title: string) {
  sessions.rename(id, title);
}

function remove(id: string) {
  if (!confirm("确定删除该会话？")) return;
  sessions.remove(id);
  if (sessions.currentId === id) {
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
      class="group relative flex items-center gap-2 rounded-md py-[6px] pl-3 pr-1.5 text-left text-[12.5px] transition"
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
      <span
        class="min-w-0 flex-1 truncate"
        :title="s.title"
        @dblclick="(e: MouseEvent) => onDblClickTitle(e, s.id)"
      >
        {{ s.title }}
      </span>
      <button
        v-if="!sessions.batchMode"
        class="hidden flex-shrink-0 text-ink-faint transition hover:text-err group-hover:block"
        title="删除会话"
        @click.stop="remove(s.id)"
      >
        <Icon name="trash" :size="13" />
      </button>
    </button>
  </div>
</template>
