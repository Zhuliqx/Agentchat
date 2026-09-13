import { ref, watch, type Ref } from "vue";

interface DialogResourceOptions {
  /** 打开期间该值变化时重新取数（例如切换当前会话）。 */
  key?: () => unknown;
  /** 每次打开时先执行的重置动作（清空表单、提示等），依赖变化时不触发。 */
  onOpen?: () => void;
}

/**
 * 弹窗取数生命周期：打开时取数并管理 loading/error。
 *
 * 弹窗普遍由 v-if 挂载（打开才创建），组件创建瞬间 open 已经是 true，
 * 普通 watch 等不到变化，首次打开就会停在空白态；这里用 immediate + 依赖数组
 * 保证「挂载即打开」同样取数。并发取数只保留最后一次结果。
 */
export function useDialogResource(
  open: Ref<boolean>,
  fetcher: () => Promise<void>,
  options: DialogResourceOptions = {},
) {
  const loading = ref(false);
  const error = ref("");
  let latest = 0;

  async function reload() {
    const run = ++latest;
    loading.value = true;
    error.value = "";
    try {
      await fetcher();
    } catch (e) {
      // 过期请求的失败不能覆盖当前状态
      if (run === latest) error.value = (e as Error).message;
    } finally {
      if (run === latest) loading.value = false;
    }
  }

  watch(
    [open, () => options.key?.()],
    ([isOpen], previous) => {
      if (!isOpen) return;
      if (!previous || !previous[0]) options.onOpen?.();
      void reload();
    },
    { immediate: true },
  );

  return { loading, error, reload };
}
