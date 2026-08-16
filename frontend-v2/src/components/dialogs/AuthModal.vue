<script setup lang="ts">
import { ref } from "vue";
import Modal from "@/components/common/Modal.vue";
import { useAuthStore } from "@/stores/auth";
import { useSessionsStore } from "@/stores/sessions";
import { useMemoryStore } from "@/stores/memory";
import { useDocsStore } from "@/stores/docs";
import { useChatStore } from "@/stores/chat";

const auth = useAuthStore();
const sessions = useSessionsStore();
const memory = useMemoryStore();
const docs = useDocsStore();
const chat = useChatStore();

const username = ref("");
const password = ref("");
const error = ref("");

function setTab(tab: "login" | "register") {
  auth.authTab = tab;
  username.value = "";
  password.value = "";
  error.value = "";
}

async function submit() {
  error.value = "";
  const u = username.value.trim();
  if (!u || !password.value) return;
  try {
    if (auth.authTab === "login") {
      await auth.login(u, password.value);
      // 登录后加载新用户数据域（重置 currentId，避免残留 guest 会话；文档也按用户隔离刷新）
      await Promise.all([sessions.load(), memory.load(), docs.load()]);
      sessions.currentId = "";
      if (sessions.list.length) {
        sessions.currentId = sessions.list[0].id;
        await chat.loadHistory(sessions.currentId);
      } else {
        await sessions.create();
        chat.clear();
      }
    } else {
      await auth.register(u, password.value);
      error.value = "注册成功，请登录";
      auth.authTab = "login";
      password.value = "";
    }
  } catch (e) {
    error.value = (e as Error).message;
  }
}

function guest() {
  auth.logoutLocal();
  auth.authOpen = false;
  // 切换回访客域：重置当前会话与聊天区，刷新文档/记忆
  sessions.currentId = "";
  chat.clear();
  sessions.load();
  memory.load();
  docs.load();
}
</script>

<template>
  <Modal :open="auth.authOpen" :small="true" title="账户" @close="auth.authOpen = false">
    <div class="mb-4 grid grid-cols-2 gap-1 rounded-lg border border-line bg-surface-2 p-1">
      <button
        class="rounded-md py-1.5 text-[13px] transition"
        :class="auth.authTab === 'login' ? 'bg-surface-3 font-medium text-ink' : 'text-ink-faint hover:text-ink-dim'"
        @click="setTab('login')"
      >
        登录
      </button>
      <button
        class="rounded-md py-1.5 text-[13px] transition"
        :class="auth.authTab === 'register' ? 'bg-surface-3 font-medium text-ink' : 'text-ink-faint hover:text-ink-dim'"
        @click="setTab('register')"
      >
        注册
      </button>
    </div>
    <form class="flex flex-col gap-3" @submit.prevent="submit">
      <label class="flex flex-col gap-1.5 text-xs text-ink-dim">
        用户名
        <input
          v-model="username"
          type="text"
          class="rounded-lg border border-line-2 bg-surface-2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
          placeholder="字母/数字/._-"
          minlength="2"
          maxlength="32"
          required
        />
      </label>
      <label class="flex flex-col gap-1.5 text-xs text-ink-dim">
        密码
        <input
          v-model="password"
          type="password"
          class="rounded-lg border border-line-2 bg-surface-2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
          placeholder="至少 6 位"
          minlength="6"
          required
        />
      </label>
      <div
        v-if="error"
        class="rounded-lg px-2.5 py-1.5 text-[13px]"
        :class="error === '注册成功，请登录' ? 'bg-ok/10 text-ok' : 'bg-err/10 text-err'"
      >
        {{ error }}
      </div>
      <button
        type="submit"
        class="rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition hover:brightness-110 disabled:opacity-60"
        :disabled="auth.loading"
      >
        {{ auth.authTab === "login" ? "登录" : "注册" }}
      </button>
      <button
        type="button"
        class="text-center text-xs text-ink-dim transition hover:text-ink"
        @click="guest"
      >
        以访客身份继续
      </button>
    </form>
  </Modal>
</template>
