/**
 * 侧边栏宽度约束与开合参数：父组件持有宽度状态、子组件负责拖拽交互，
 * 两侧共用同一组常量，避免阈值与上下限各写一份而漂移。
 */

/** 可读的最小宽度，同时也是拖拽收窄到自动折叠的阈值 */
export const SIDEBAR_MIN_WIDTH = 180;
export const SIDEBAR_MAX_WIDTH = 480;
export const SIDEBAR_DEFAULT_WIDTH = 236;

/** 读取持久化宽度并钳制到合法区间（localStorage 里可能是历史值或脏数据） */
export function clampSidebarWidth(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n) || n <= 0) return SIDEBAR_DEFAULT_WIDTH;
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(n)));
}
