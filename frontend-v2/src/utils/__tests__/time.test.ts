import { describe, expect, it } from "vitest";
import { absoluteTime, dayBucket, relativeTime } from "@/utils/time";

const NOW = new Date("2026-09-13T16:00:00Z").getTime();
const ago = (ms: number) => new Date(NOW - ms).toISOString();

describe("relativeTime", () => {
  it("按距离分档：刚刚 / 分钟 / 小时 / 昨天 / 月-日", () => {
    expect(relativeTime(ago(10_000), NOW)).toBe("刚刚");
    expect(relativeTime(ago(60_000), NOW)).toBe("1 分钟前");
    expect(relativeTime(ago(45 * 60_000), NOW)).toBe("45 分钟前");
    expect(relativeTime(ago(3 * 3_600_000), NOW)).toBe("3 小时前");
    expect(relativeTime(ago(30 * 3_600_000), NOW)).toBe("昨天");
    // 更早的按本地日期显示（不同时区下日期可能不同，所以用同一时间戳推导期望值）
    const tenDaysAgo = new Date(NOW - 10 * 86_400_000);
    expect(relativeTime(tenDaysAgo.toISOString(), NOW)).toBe(
      `${tenDaysAgo.getMonth() + 1}-${tenDaysAgo.getDate()}`,
    );
  });

  it("跨年带年份；空值与非法值返回空串", () => {
    expect(relativeTime("2025-12-01T00:00:00Z", NOW)).toBe("2025-12-1");
    expect(relativeTime(undefined, NOW)).toBe("");
    expect(relativeTime("not-a-date", NOW)).toBe("");
  });

  it("时钟偏差（未来时间）按刚刚处理", () => {
    expect(relativeTime(new Date(NOW + 5_000).toISOString(), NOW)).toBe("刚刚");
  });
});

describe("absoluteTime", () => {
  it("输出可读的绝对时间", () => {
    const iso = new Date(2026, 8, 13, 16, 20).toISOString();
    expect(absoluteTime(iso)).toBe("2026-09-13 16:20");
    expect(absoluteTime(null)).toBe("");
  });
});

describe("dayBucket", () => {
  /** 本地时间的 N 天前（用日历日推算，避免"现在几点"影响期望值） */
  const daysAgo = (n: number, hour = 10) => {
    const d = new Date(NOW);
    d.setDate(d.getDate() - n);
    d.setHours(hour, 0, 0, 0);
    return d.toISOString();
  };

  it("按自然日分档：今天 / 昨天 / 更早", () => {
    expect(dayBucket(daysAgo(0), NOW)).toBe("today");
    expect(dayBucket(daysAgo(1), NOW)).toBe("yesterday");
    expect(dayBucket(daysAgo(2), NOW)).toBe("earlier");
    expect(dayBucket(daysAgo(40), NOW)).toBe("earlier");
  });

  it("时钟偏差的未来时间算今天；空值/非法值算更早", () => {
    expect(dayBucket(new Date(NOW + 3_600_000).toISOString(), NOW)).toBe("today");
    expect(dayBucket(null, NOW)).toBe("earlier");
    expect(dayBucket("not-a-date", NOW)).toBe("earlier");
  });
});
