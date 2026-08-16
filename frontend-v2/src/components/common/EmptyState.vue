<script setup lang="ts">
import Icon from "./Icon.vue";
defineProps<{ text?: string; icon?: string }>();
</script>

<template>
  <div class="flex flex-col items-center gap-1.5 px-2 py-4">
    <Icon :name="icon || 'inbox'" :size="16" class="text-ink-faint" />
    <span class="text-[11.5px] text-ink-faint">{{ text ?? "暂无数据" }}</span>
  </div>
</template>
