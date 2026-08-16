<script setup lang="ts">
import { ref, watch } from "vue";
import Modal from "@/components/common/Modal.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import Icon from "@/components/common/Icon.vue";
import { useTasksStore } from "@/stores/tasks";

const open = defineModel<boolean>({ default: false });
const tasks = useTasksStore();
const showForm = ref(false);
const form = ref({ name: "", task_type: "", schedule: "interval:3600" });

watch(open, (v) => {
  if (v) {
    tasks.load().then(() => {
      // select 的 v-model 不会自动选中第一个 option，需手动同步
      if (!form.value.task_type && tasks.registry.length) {
        form.value.task_type = tasks.registry[0].type;
      }
    });
    showForm.value = false;
  }
});

async function saveTask() {
  if (!form.value.name.trim()) return alert("请填写任务名称");
  try {
    await tasks.create(form.value.name.trim(), form.value.task_type, form.value.schedule.trim());
    showForm.value = false;
    form.value = { name: "", task_type: form.value.task_type, schedule: "interval:3600" };
  } catch (e) {
    alert("创建失败：" + (e as Error).message);
  }
}
</script>

<template>
  <Modal :open="open" title="定时任务" @close="open = false">
    <div class="mb-3 flex items-center gap-2">
      <button
        class="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[12px] font-medium text-white transition hover:brightness-110"
        @click="showForm = !showForm"
      >
        <Icon name="plus" :size="13" />
        新建任务
      </button>
      <button
        class="flex items-center gap-1.5 rounded-lg border border-line-2 px-3 py-1.5 text-[12px] text-ink-dim transition hover:text-ink"
        @click="tasks.load()"
      >
        <Icon name="refresh" :size="13" />
        刷新
      </button>
    </div>

    <!-- 新建表单 -->
    <div v-if="showForm" class="mb-3 flex flex-col gap-3 rounded-xl border border-line bg-surface-2 p-3.5">
      <h4 class="text-[13px] font-medium">新建任务</h4>
      <label class="flex flex-col gap-1.5 text-[12px] text-ink-faint">
        任务名称
        <input v-model="form.name" class="rounded-lg border border-line-2 bg-surface px-3 py-1.5 text-[13px] text-ink outline-none placeholder:text-ink-faint focus:border-accent" placeholder="如：每夜重建索引" maxlength="100" />
      </label>
      <label class="flex flex-col gap-1.5 text-[12px] text-ink-faint">
        任务类型
        <select v-model="form.task_type" class="rounded-lg border border-line-2 bg-surface px-3 py-1.5 text-[13px] text-ink outline-none focus:border-accent">
          <option v-for="r in tasks.registry" :key="r.type" :value="r.type">{{ r.label }} — {{ r.desc }}</option>
        </select>
      </label>
      <label class="flex flex-col gap-1.5 text-[12px] text-ink-faint">
        调度表达式
        <input v-model="form.schedule" class="rounded-lg border border-line-2 bg-surface px-3 py-1.5 font-mono text-[13px] text-ink outline-none focus:border-accent" placeholder="interval:3600 或 cron:*/30" />
        <small class="text-[10.5px] text-ink-faint">interval:&lt;秒&gt; 固定间隔；cron:&lt;分钟&gt; 分钟级（如 */30、0）</small>
      </label>
      <div class="flex gap-2">
        <button class="rounded-lg bg-accent px-3.5 py-1.5 text-[12px] font-medium text-white transition hover:brightness-110" @click="saveTask">保存</button>
        <button class="rounded-lg border border-line-2 px-3.5 py-1.5 text-[12px] text-ink-dim transition hover:text-ink" @click="showForm = false">取消</button>
      </div>
    </div>

    <!-- 任务列表 -->
    <div class="flex max-h-[55vh] flex-col gap-2 overflow-y-auto pr-1">
      <EmptyState v-if="!tasks.list.length" text="暂无任务" icon="tasks" />
      <div v-for="t in tasks.list" :key="t.id" class="rounded-xl border border-line bg-surface-2 p-3.5">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-[13px] font-medium text-ink">{{ t.name }}</span>
          <span class="rounded-full bg-accent/12 px-2 py-0.5 text-[10.5px] font-medium text-accent">{{ t.task_label }}</span>
          <span class="ml-auto flex items-center gap-1 text-[10.5px]" :class="t.enabled ? 'text-ok' : 'text-ink-faint'">
            <span :class="['h-1.5 w-1.5 rounded-full', t.enabled ? 'bg-ok' : 'bg-ink-faint']" />
            {{ t.enabled ? "启用" : "停用" }}
          </span>
        </div>
        <div class="mt-1 text-[12px] leading-relaxed text-ink-dim">{{ t.task_desc }}</div>
        <div class="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-faint">
          <span>调度：<code class="rounded bg-surface-3 px-1 py-0.5 font-mono text-ink-dim">{{ t.schedule }}</code></span>
          <span v-if="t.next_run_at">下次 {{ new Date(t.next_run_at).toLocaleString() }}</span>
          <span v-if="t.last_run_at">上次 {{ new Date(t.last_run_at).toLocaleString() }}</span>
          <span v-if="t.last_status">
            结果
            <b :class="t.last_status === 'success' ? 'text-ok' : t.last_status === 'failed' ? 'text-err' : 'text-warn'">
              {{ t.last_status === "success" ? "成功" : t.last_status === "failed" ? "失败" : t.last_status }}
            </b>
          </span>
        </div>
        <div v-if="t.last_error" class="mt-1 break-all text-[11.5px] text-err">{{ t.last_error }}</div>
        <div class="mt-2.5 flex flex-wrap gap-1.5">
          <button class="flex items-center gap-1 rounded-md border border-line-2 px-2 py-1 text-[11px] text-ink-dim transition hover:border-accent/50 hover:text-ink" @click="tasks.run(t.id)">
            <Icon name="play" :size="11" />
            立即执行
          </button>
          <button class="flex items-center gap-1 rounded-md border border-line-2 px-2 py-1 text-[11px] text-ink-dim transition hover:border-accent/50 hover:text-ink" @click="tasks.update(t.id, { enabled: !t.enabled })">
            <Icon :name="t.enabled ? 'pause' : 'play'" :size="11" />
            {{ t.enabled ? "停用" : "启用" }}
          </button>
          <button class="flex items-center gap-1 rounded-md border border-err/30 px-2 py-1 text-[11px] text-err transition hover:bg-err/10" @click="tasks.remove(t.id)">
            <Icon name="trash" :size="11" />
            删除
          </button>
        </div>
      </div>
    </div>
  </Modal>
</template>
