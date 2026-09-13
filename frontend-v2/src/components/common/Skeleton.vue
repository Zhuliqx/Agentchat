<script setup lang="ts">
withDefaults(defineProps<{ rows?: number }>(), { rows: 3 });
</script>

<template>
  <!-- 骨架屏只表达"正在取数"，具体文案由使用方提供 sr-only 文本给读屏 -->
  <div class="flex flex-col gap-2" aria-hidden="true">
    <span
      v-for="i in rows"
      :key="i"
      class="block h-3 animate-pulse rounded bg-surface-3"
      :class="i === rows ? 'w-2/3' : 'w-full'"
    />
  </div>
</template>
