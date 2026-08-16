<script setup lang="ts">
import { ref } from "vue";
import { useMemoryStore } from "@/stores/memory";
import EmptyState from "@/components/common/EmptyState.vue";
import Icon from "@/components/common/Icon.vue";

const memory = useMemoryStore();
const input = ref("");
const search = ref("");
let debounceTimer: number | undefined;

function onSearch() {
  clearTimeout(debounceTimer);
  debounceTimer = window.setTimeout(() => memory.load(search.value), 250);
}

async function add() {
  const c = input.value.trim();
  if (!c) return;
  await memory.add(c);
  input.value = "";
}

async function remove(id: string) {
  await memory.remove(id);
}
</script>

<template>
  <div>
    <div class="relative mb-1.5">
      <Icon name="search" :size="12" class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint" />
      <input
        v-model="search"
        type="text"
        class="w-full rounded-lg border border-line-2 bg-surface-2 py-1.5 pl-7 pr-2.5 text-[12px] text-ink outline-none placeholder:text-ink-faint focus:border-accent"
        placeholder="搜索记忆…"
        @input="onSearch"
      />
    </div>
    <div class="mb-1.5 flex gap-1.5">
      <input
        v-model="input"
        type="text"
        class="min-w-0 flex-1 rounded-lg border border-line-2 bg-surface-2 px-2.5 py-1.5 text-[12px] text-ink outline-none placeholder:text-ink-faint focus:border-accent"
        placeholder="记住一条信息…"
        @keydown.enter="add"
      />
      <button
        class="grid h-[30px] w-[30px] flex-shrink-0 place-items-center rounded-lg border border-line-2 text-ink-dim transition hover:border-accent/50 hover:text-ink"
        title="添加记忆"
        @click="add"
      >
        <Icon name="plus" :size="13" />
      </button>
    </div>
    <div v-if="memory.list.length" class="flex flex-col gap-px">
      <div
        v-for="m in memory.list"
        :key="m.id"
        class="group flex items-center gap-2 rounded-md px-1.5 py-[5px] text-[12px] text-ink-dim transition hover:bg-surface-2 hover:text-ink"
        :title="m.content"
      >
        <Icon name="brain" :size="13" class="flex-shrink-0 text-ink-faint" />
        <span class="min-w-0 flex-1 truncate">{{ m.content }}</span>
        <button
          class="hidden flex-shrink-0 text-ink-faint transition hover:text-err group-hover:block"
          title="删除记忆"
          @click="remove(m.id)"
        >
          <Icon name="x" :size="12" />
        </button>
      </div>
    </div>
    <EmptyState v-else text="暂无记忆" />
  </div>
</template>
