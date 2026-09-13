import { onBeforeUnmount } from "vue";

export interface DragContext {
  startX: number;
  startY: number;
}

/**
 * 指针拖拽：按下后监听 window 的 move/up，拖出把手或窗口也不会丢事件。
 * 相比 mouse 事件同时覆盖触摸与手写笔；配合 touch-none 使用。
 */
export function usePointerDrag(handlers: {
  onStart?: () => void;
  onMove: (e: PointerEvent, ctx: DragContext) => void;
  onEnd?: () => void;
}) {
  let stopActive: (() => void) | null = null;

  function start(e: PointerEvent) {
    e.preventDefault();
    (e.currentTarget as HTMLElement | null)?.setPointerCapture?.(e.pointerId);
    const ctx: DragContext = { startX: e.clientX, startY: e.clientY };
    handlers.onStart?.();

    const onMove = (ev: PointerEvent) => handlers.onMove(ev, ctx);
    const stop = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      stopActive = null;
      handlers.onEnd?.();
    };
    stopActive = stop;
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  }

  // 卸载时若仍在拖拽，收尾清理监听
  onBeforeUnmount(() => stopActive?.());
  return start;
}
