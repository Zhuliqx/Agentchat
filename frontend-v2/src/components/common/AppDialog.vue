<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import Modal from "./Modal.vue";
import { useDialogStore } from "@/stores/dialog";

const dialog = useDialogStore();
const input = ref("");
const inputEl = ref<HTMLInputElement | null>(null);

watch(
  () => dialog.current,
  async (current) => {
    if (current?.kind !== "prompt") return;
    input.value = current.value;
    await nextTick();
    inputEl.value?.focus();
    inputEl.value?.select();
  },
);

function accept() {
  const current = dialog.current;
  if (!current) return;
  if (current.kind === "prompt") dialog.resolve(input.value);
  else if (current.kind === "confirm") dialog.resolve(true);
  else dialog.resolve(undefined);
}

function cancel() {
  const current = dialog.current;
  if (!current) return;
  dialog.resolve(current.kind === "prompt" ? null : false);
}
</script>

<template>
  <Modal :open="!!dialog.current" :title="dialog.current?.title || ''" small @close="cancel">
    <p class="whitespace-pre-wrap text-[13px] leading-relaxed text-ink-dim">
      {{ dialog.current?.message }}
    </p>
    <input
      v-if="dialog.current?.kind === 'prompt'"
      ref="inputEl"
      v-model="input"
      data-testid="dialog-input"
      class="mt-3 w-full rounded-lg border border-line-2 bg-surface-2 px-3 py-2 text-[13px] text-ink outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/15"
      @keydown.enter="accept"
    />
    <div class="mt-4 flex justify-end gap-2">
      <button
        v-if="dialog.current?.kind !== 'alert'"
        class="h-8 rounded-lg border border-line-2 px-3.5 text-[12.5px] text-ink-dim transition hover:border-line hover:text-ink"
        @click="cancel"
      >
        取消
      </button>
      <button
        data-testid="dialog-confirm"
        class="h-8 rounded-lg bg-accent px-4 text-[12.5px] font-medium text-white transition hover:brightness-110"
        @click="accept"
      >
        确定
      </button>
    </div>
  </Modal>
</template>
