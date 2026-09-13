<script setup lang="ts">
import Skeleton from "./Skeleton.vue";

withDefaults(defineProps<{ loading?: boolean; error?: string; loadingRows?: number }>(), {
  loading: false,
  error: "",
});
</script>

<template>
  <div v-if="loading" class="py-1">
    <Skeleton :rows="loadingRows ?? 4" />
    <span class="sr-only">加载中…</span>
  </div>
  <p v-else-if="error" class="text-sm text-err">{{ error }}</p>
  <slot v-else />
</template>
