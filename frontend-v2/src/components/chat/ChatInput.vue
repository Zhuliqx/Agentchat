<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from "vue";
import { useChatStore } from "@/stores/chat";
import { useAuthStore } from "@/stores/auth";
import { useChatOptionsStore } from "@/stores/chatOptions";
import { useModelStore } from "@/stores/model";
import { useSessionsStore } from "@/stores/sessions";
import Icon from "@/components/common/Icon.vue";
import Switch from "@/components/common/Switch.vue";
import Dropdown from "@/components/common/Dropdown.vue";
import Tooltip from "@/components/common/Tooltip.vue";

const chat = useChatStore();
const auth = useAuthStore();
const options = useChatOptionsStore();
const model = useModelStore();
const sessions = useSessionsStore();
const input = ref("");
const inputEl = ref<HTMLTextAreaElement | null>(null);
const modelOpen = ref(false);

// ---- 输入草稿：按会话持久化到 localStorage（刷新/切换不丢失） ----
const draftKey = (sid: string) => `chat_draft_${sid}`;
function loadDraft() {
  input.value = sessions.currentId ? localStorage.getItem(draftKey(sessions.currentId)) || "" : "";
  autoResize();
}

let lastSid = sessions.currentId;
// 切换会话：先把当前草稿存入旧会话，再恢复新会话草稿
watch(
  () => sessions.currentId,
  (newId) => {
    if (lastSid) localStorage.setItem(draftKey(lastSid), input.value);
    lastSid = newId;
    input.value = newId ? localStorage.getItem(draftKey(newId)) || "" : "";
    autoResize();
  },
);

// 进入分支态时把光标交给输入框，用户直接输入新消息即可
watch(
  () => chat.branchFrom,
  (branch) => {
    if (branch) inputEl.value?.focus();
  },
);

onMounted(() => {
  model.load();
  loadDraft();
});

function autoResize() {
  const el = inputEl.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

function onInput() {
  autoResize();
  if (sessions.currentId) {
    localStorage.setItem(draftKey(sessions.currentId), input.value);
  }
}

async function send() {
  const text = input.value.trim();
  if (!text || chat.sending) return;
  input.value = "";
  autoResize();
  // 发送后清除该会话草稿
  if (sessions.currentId) {
    localStorage.removeItem(draftKey(sessions.currentId));
  }
  if (chat.branchFrom) {
    await chat.branchAndSend(text);
    return;
  }
  await chat.send(text, {
    useRag: options.useRag,
    useSearch: options.useSearch,
    useMemory: options.useMemory,
  });
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
  nextTick(autoResize);
}

async function chooseModel(id: string) {
  modelOpen.value = false;
  await model.set(id);
}
</script>

<template>
  <!-- 悬浮于会话区之上的毛玻璃输入框 -->
  <!-- 渐变底衬：悬浮输入区下面的滚动内容不会与工具条文字相互干扰 -->
  <div
    class="pointer-events-none absolute inset-x-0 bottom-0 z-10 bg-gradient-to-t from-bg via-bg/85 to-transparent pb-4 pt-6"
  >
    <div class="content-col pointer-events-auto">
      <!-- 回到最新：贴在输入框上方（落在渐变底衬里），不遮挡正文 -->
      <div v-if="chat.showJumpButton" class="mb-2 flex justify-center">
        <button
          class="flex h-8 items-center gap-1.5 rounded-full border border-line-2 bg-surface/90 px-3 text-2xs text-ink-dim shadow-[0_8px_24px_rgba(0,0,0,0.35)] backdrop-blur transition hover:border-accent/50 hover:text-ink"
          @click="chat.jumpToBottom()"
        >
          <Icon name="chevron" :size="12" />
          {{ chat.unseen > 0 ? `${chat.unseen} 条新消息` : "回到最新" }}
        </button>
      </div>
      <!-- 分支态：说明发送后会被删除的后续消息，可随时取消 -->
      <div
        v-if="chat.branchFrom"
        data-testid="branch-bar"
        class="mb-2 flex items-center gap-2 rounded-xl border border-accent/30 bg-accent/10 px-3 py-1.5 text-2xs text-ink-dim"
      >
        <Icon name="branch" :size="12" class="flex-shrink-0 text-accent" />
        <span class="min-w-0 flex-1 truncate">
          正在从此消息分支{{
            chat.branchAfterCount ? `，发送后将删除其后的 ${chat.branchAfterCount} 条消息` : ""
          }}
        </span>
        <button
          class="flex-shrink-0 rounded-md px-1.5 py-0.5 transition hover:bg-surface-2 hover:text-ink"
          aria-label="取消分支"
          @click="chat.cancelBranch()"
        >
          取消
        </button>
      </div>
      <div
        class="relative flex items-end gap-2 rounded-2xl border border-line/60 bg-surface-2/50 shadow-[0_8px_32px_rgba(0,0,0,0.28)] backdrop-blur-xl transition focus-within:border-accent focus-within:bg-surface-2/70 focus-within:shadow-[0_0_0_3px_rgba(74,125,255,0.15),0_8px_32px_rgba(0,0,0,0.28)]"
      >
        <textarea
          ref="inputEl"
          v-model="input"
          rows="1"
          class="min-h-[46px] flex-1 resize-none bg-transparent px-4 py-[13px] text-base leading-relaxed text-ink outline-none placeholder:text-ink-faint"
          :placeholder="
            chat.branchFrom
              ? '输入新消息，发送后从这里开始新分支'
              : '输入消息，Enter 发送，Shift+Enter 换行'
          "
          @input="onInput"
          @keydown="onKeydown"
        />
        <Tooltip v-if="!chat.sending" label="发送" class="mb-1.5 mr-1.5 flex-shrink-0">
          <button
            class="grid h-8 w-8 place-items-center rounded-xl text-white transition disabled:bg-transparent disabled:text-ink-faint"
            :class="input.trim() ? 'bg-accent hover:brightness-110' : ''"
            :disabled="!input.trim()"
            aria-label="发送"
            @click="send"
          >
            <Icon name="send" :size="15" />
          </button>
        </Tooltip>
        <Tooltip v-else label="停止生成" class="mb-1.5 mr-1.5 flex-shrink-0">
          <button
            class="grid h-8 w-8 place-items-center rounded-xl border border-err/40 text-err transition hover:bg-err/10"
            aria-label="停止生成"
            @click="chat.stop()"
          >
            <Icon name="stop" :size="13" />
          </button>
        </Tooltip>
      </div>

      <!-- 输入框下方工具条：模型与能力开关（知识库/记忆/联网）统一收在这里 -->
      <div class="mt-1.5 flex items-center justify-between gap-2 px-0.5">
        <div class="flex min-w-0 items-center gap-2">
          <Dropdown
            v-if="auth.platformOperator !== false"
            :open="modelOpen"
            align="left"
            @close="modelOpen = false"
          >
            <template #trigger>
              <button
                class="flex h-6 max-w-[150px] items-center gap-1 rounded-md border border-line bg-surface-2/60 px-1.5 text-2xs text-ink-faint transition hover:border-line-2 hover:bg-surface-2 hover:text-ink"
                title="切换模型"
                @click="modelOpen = !modelOpen"
              >
                <Icon name="zap" :size="12" class="flex-shrink-0" />
                <span class="min-w-0 flex-1 truncate">{{ model.currentShort || "模型" }}</span>
                <Icon
                  name="chevron"
                  :size="9"
                  class="flex-shrink-0 transition-transform duration-200"
                  :class="modelOpen ? 'rotate-180' : ''"
                />
              </button>
            </template>
            <template #default>
              <div class="max-h-[260px] overflow-y-auto">
                <button
                  v-for="m in model.models"
                  :key="m.id"
                  class="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition hover:bg-surface-2"
                  @click="chooseModel(m.id)"
                >
                  <span
                    class="min-w-0 flex-1 truncate"
                    :class="m.id === model.currentId ? 'font-medium text-accent' : 'text-ink-dim'"
                  >
                    {{ m.label }}
                  </span>
                  <Icon
                    v-if="m.id === model.currentId"
                    name="check"
                    :size="12"
                    class="flex-shrink-0 text-accent"
                  />
                </button>
                <div v-if="!model.models.length" class="px-2.5 py-2 text-xs text-ink-faint">
                  暂无可用模型
                </div>
              </div>
            </template>
          </Dropdown>

          <Switch
            :model-value="options.useRag"
            @update:model-value="(v: boolean) => (options.useRag = v)"
          >
            <span class="text-2xs">知识库</span>
          </Switch>
          <Switch
            :model-value="options.useMemory"
            @update:model-value="(v: boolean) => (options.useMemory = v)"
          >
            <span class="text-2xs">记忆</span>
          </Switch>
        </div>

        <Switch
          :model-value="options.useSearch"
          @update:model-value="(v: boolean) => (options.useSearch = v)"
        >
          <span class="text-2xs">联网</span>
        </Switch>
      </div>
    </div>
  </div>
</template>
