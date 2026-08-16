/* 自定义滚动条（淡入淡出 + 悬停不变色）。 */
"use strict";

// 原生 ::-webkit-scrollbar 伪元素不支持 transition 渐变，改用 DOM 覆盖层实现：
// 平时 opacity:0 隐藏；滚动或悬停容器时淡入，停止滚动/离开后延迟淡出；
// thumb 颜色恒定（悬停滚动条本身不变色）。
// 注意：列表/消息区会通过 innerHTML 重渲染清掉轨道子节点，因此容器事件只绑定一次，
// 轨道由文档级 MutationObserver 兜底自动重建。
const _sbTimers = new WeakMap();

export function initCustomScrollbars() {
  const targets = Array.from(
    document.querySelectorAll("#messages, .sidebar, .session-list, .doc-list, .memory-list")
  );

  const scrollable = (el) => el.scrollHeight > el.clientHeight + 1;

  function update(el) {
    const track = el._sbTrack;
    if (!track || !track.isConnected) return;
    // fixed 视口定位：把轨道钉在容器可视区，不随内容滚动、不参与容器滚动溢出
    // （避免 absolute+transform 方案撑大 scrollHeight 导致“到底了还能往下滑”）
    const rect = el.getBoundingClientRect();
    track.style.top = rect.top + 4 + "px";
    track.style.left = rect.right - 8 + "px"; // 容器右缘 -2px，轨道宽 6px
    track.style.height = el.clientHeight - 8 + "px";
    const thumb = track.firstElementChild;
    const h = el.clientHeight;
    const th = Math.max(22, Math.min(h, (h / el.scrollHeight) * h));
    const maxTop = Math.max(0, (h - 8) - th);
    const ratio = el.scrollHeight > h ? el.scrollTop / (el.scrollHeight - h) : 0;
    thumb.style.height = th + "px";
    thumb.style.transform = `translateY(${Math.max(0, Math.min(maxTop, ratio * maxTop))}px)`;
  }
  function show(el) {
    if (!scrollable(el)) { hide(el); return; }
    update(el);
    el._sbTrack?.classList.add("show"); // opacity 0→1 触发淡入
  }
  function hide(el) {
    el._sbTrack?.classList.remove("show"); // opacity 1→0 触发淡出
  }
  function scheduleHide(el, delay = 900) {
    clearTimeout(_sbTimers.get(el));
    _sbTimers.set(el, setTimeout(() => hide(el), delay));
  }
  function cancelHide(el) {
    clearTimeout(_sbTimers.get(el));
  }

  // 容器级监听：只绑定一次（innerHTML 重渲染不影响 el 自身的事件）
  function wireContainer(el) {
    if (el._sbWired) return;
    el._sbWired = true;

    // 滚动：更新并淡入；停止滚动 0.9s 且鼠标不在容器上时淡出
    el.addEventListener("scroll", () => {
      if (!scrollable(el)) { hide(el); return; }
      update(el);
      if (!el._sbTrack?.classList.contains("show")) show(el);
      if (el.matches(":hover")) cancelHide(el);
      else scheduleHide(el);
    }, { passive: true });

    // 悬停容器：常显（鼠标离开容器即淡出）
    el.addEventListener("mouseenter", () => { cancelHide(el); show(el); });
    el.addEventListener("mouseleave", () => hide(el));
  }

  // 轨道构建（被 innerHTML 清掉后重建，并重接轨道级事件与观察器）
  function buildTrack(el) {
    if (el._sbTrack && el._sbTrack.isConnected) return; // 轨道仍在，无需重建
    if (el._sbRO) el._sbRO.disconnect();
    if (el._sbMO) el._sbMO.disconnect();

    const track = document.createElement("div");
    track.className = "custom-scrollbar";
    const thumb = document.createElement("div");
    thumb.className = "custom-scrollbar-thumb";
    track.appendChild(thumb);
    el.appendChild(track);
    el._sbTrack = track;

    // 悬停滚动条轨道：保持可见；离开轨道后（若已不在容器上）延迟淡出
    track.addEventListener("mouseenter", () => { cancelHide(el); show(el); });
    track.addEventListener("mouseleave", () => {
      if (!el.matches(":hover")) scheduleHide(el, 350);
    });

    // 拖动 thumb / 点击轨道跳转滚动
    const scrollToRatio = (clientY) => {
      const rect = track.getBoundingClientRect();
      const ratio = (clientY - rect.top) / Math.max(1, rect.height);
      el.scrollTop = Math.max(0, Math.min(1, ratio)) * (el.scrollHeight - el.clientHeight);
    };
    let dragging = false;
    thumb.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dragging = true;
      cancelHide(el);
      scrollToRatio(e.clientY);
      const onMove = (ev) => { if (dragging) scrollToRatio(ev.clientY); };
      const onUp = () => {
        dragging = false;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        if (!el.matches(":hover")) scheduleHide(el, 700);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    });
    track.addEventListener("pointerdown", (e) => {
      if (e.target === thumb) return; // thumb 自行处理
      scrollToRatio(e.clientY);
      show(el);
    });

    // 内容/尺寸变化：rAF 去抖后刷新 thumb（消息流式追加、列表重渲染都会触发）
    let rafPending = false;
    const requestUpdate = () => {
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        if (scrollable(el)) update(el);
        else hide(el);
      });
    };
    el._sbRO = new ResizeObserver(requestUpdate);
    el._sbRO.observe(el);
    el._sbMO = new MutationObserver(requestUpdate);
    el._sbMO.observe(el, { childList: true, characterData: true, subtree: true });

    // 重建后先定位轨道（fixed 视口坐标），再按悬停/可滚动状态决定显隐
    update(el);
    // 重建时若鼠标正悬停在容器上且内容可滚动，直接恢复显示
    if (el.matches(":hover") && scrollable(el)) show(el);
  }

  targets.forEach((el) => { wireContainer(el); buildTrack(el); });

  // 兜底：任何 DOM 变化后检查轨道是否被 innerHTML 清掉（去抖，自动重建）
  // 注意：用 setTimeout 而非 rAF——浏览器会暂停不可见/后台标签页的 rAF，
  // 导致轨道无法自动重建（本机 VS Code 内置浏览器在页面未激活时即如此）。
  let sweepPending = false;
  const sweep = () => {
    if (sweepPending) return;
    sweepPending = true;
    setTimeout(() => {
      sweepPending = false;
      targets.forEach(buildTrack);
    }, 0);
  };
  new MutationObserver(sweep).observe(document.body, { childList: true, subtree: true });
}
