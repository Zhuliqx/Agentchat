<script setup lang="ts">
defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ "update:modelValue": [v: boolean] }>();
</script>

<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    class="flex items-center gap-1.5 whitespace-nowrap text-[11.5px] text-ink-faint transition hover:text-ink-dim"
    @click="emit('update:modelValue', !modelValue)"
  >
    <span
      class="relative h-[16px] w-[28px] flex-shrink-0 rounded-full border transition-colors"
      :class="
        modelValue
          ? 'border-accent bg-accent'
          : 'border-line-2 bg-surface-3'
      "
    >
      <span
        class="absolute top-[2px] h-[10px] w-[10px] rounded-full transition-all"
        :class="modelValue ? 'left-[14px] bg-white' : 'left-[2px] bg-ink-faint'"
      />
    </span>
    <slot />
  </button>
</template>
