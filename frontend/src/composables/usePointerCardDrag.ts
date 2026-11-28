import { onUnmounted, ref, type Ref } from "vue";

export interface PointerCardDragOptions<T extends string | number> {
  containerRef: Ref<HTMLElement | null>;
  onReorder: (sourceId: T, targetId: T, position: "before" | "after") => void;
  deserializeId?: (raw: string) => T;
}

/**
 * 基于 Pointer Events 的通用卡片拖曳排序组合式函数。
 * 兼容桌面端鼠标与移动端触控，按住手柄或标题栏拖动时不触发文本选中。
 */
export function usePointerCardDrag<T extends string | number>(
  options: PointerCardDragOptions<T>
) {
  const draggingId = ref<T | null>(null) as Ref<T | null>;
  const dragOverId = ref<T | null>(null) as Ref<T | null>;
  const dragOverPosition = ref<"before" | "after">("before");
  const dragDeltaX = ref(0);
  const dragDeltaY = ref(0);

  let pendingId: T | null = null;
  let activePointerId: number | null = null;
  let captureEl: HTMLElement | null = null;
  let startClientX = 0;
  let startClientY = 0;
  let startedDrag = false;
  let scrollRafId: number | null = null;
  let lastClientX = 0;
  let lastClientY = 0;

  function clearSelection(): void {
    try {
      window.getSelection()?.removeAllRanges();
    } catch {
      // 忽略选区清理异常
    }
  }

  function findScrollParent(el: HTMLElement | null): HTMLElement | null {
    let cur = el?.parentElement || null;
    while (cur) {
      const style = getComputedStyle(cur);
      if (
        (style.overflowY === "auto" || style.overflowY === "scroll") &&
        cur.scrollHeight > cur.clientHeight
      ) {
        return cur;
      }
      cur = cur.parentElement;
    }
    return null;
  }

  function updateDropTarget(clientX: number, clientY: number): void {
    const container = options.containerRef.value;
    if (!container || draggingId.value === null) {
      return;
    }
    const items = [...container.querySelectorAll<HTMLElement>("[data-drag-id]")];
    let bestId: T | null = null;
    let bestPos: "before" | "after" = "before";
    let bestScore = Infinity;

    for (const el of items) {
      const rawId = el.dataset.dragId;
      if (rawId === undefined) {
        continue;
      }
      const parsedId = options.deserializeId
        ? options.deserializeId(rawId)
        : (rawId as T);
      if (parsedId === draggingId.value) {
        continue;
      }
      const rect = el.getBoundingClientRect();
      const insideX = clientX >= rect.left - 8 && clientX <= rect.right + 8;
      const insideY = clientY >= rect.top - 8 && clientY <= rect.bottom + 8;

      if (insideX && insideY) {
        bestId = parsedId;
        const midY = rect.top + rect.height / 2;
        bestPos = clientY < midY ? "before" : "after";
        bestScore = -1;
        break;
      }

      const cx = (rect.left + rect.right) / 2;
      const cy = (rect.top + rect.bottom) / 2;
      const dist = Math.hypot(clientX - cx, clientY - cy);
      if (dist < bestScore) {
        bestScore = dist;
        bestId = parsedId;
        bestPos = clientY < cy ? "before" : "after";
      }
    }

    dragOverId.value = bestId;
    dragOverPosition.value = bestPos;
  }

  function stepEdgeAutoScroll(): void {
    if (!startedDrag || draggingId.value === null) {
      scrollRafId = null;
      return;
    }
    const scrollBox = findScrollParent(options.containerRef.value);
    const edgeThreshold = 48;
    const maxSpeed = 12;

    if (scrollBox) {
      const boxRect = scrollBox.getBoundingClientRect();
      if (lastClientY < boxRect.top + edgeThreshold) {
        const ratio = Math.min(1, (boxRect.top + edgeThreshold - lastClientY) / edgeThreshold);
        scrollBox.scrollTop -= Math.max(2, Math.round(maxSpeed * ratio));
        updateDropTarget(lastClientX, lastClientY);
      } else if (lastClientY > boxRect.bottom - edgeThreshold) {
        const ratio = Math.min(1, (lastClientY - (boxRect.bottom - edgeThreshold)) / edgeThreshold);
        scrollBox.scrollTop += Math.max(2, Math.round(maxSpeed * ratio));
        updateDropTarget(lastClientX, lastClientY);
      }
    } else {
      if (lastClientY < edgeThreshold) {
        const ratio = Math.min(1, (edgeThreshold - lastClientY) / edgeThreshold);
        window.scrollBy(0, -Math.max(2, Math.round(maxSpeed * ratio)));
        updateDropTarget(lastClientX, lastClientY);
      } else if (lastClientY > window.innerHeight - edgeThreshold) {
        const ratio = Math.min(
          1,
          (lastClientY - (window.innerHeight - edgeThreshold)) / edgeThreshold
        );
        window.scrollBy(0, Math.max(2, Math.round(maxSpeed * ratio)));
        updateDropTarget(lastClientX, lastClientY);
      }
    }

    scrollRafId = window.requestAnimationFrame(stepEdgeAutoScroll);
  }

  function cleanupListeners(): void {
    window.removeEventListener("pointermove", handleWindowPointerMove);
    window.removeEventListener("pointerup", handleWindowPointerUp);
    window.removeEventListener("pointercancel", handleWindowPointerCancel);
    window.removeEventListener("keydown", handleWindowKeydown);
    document.body.classList.remove("is-pointer-dragging");
    if (scrollRafId !== null) {
      window.cancelAnimationFrame(scrollRafId);
      scrollRafId = null;
    }
    if (captureEl && activePointerId !== null) {
      try {
        if (captureEl.hasPointerCapture(activePointerId)) {
          captureEl.releasePointerCapture(activePointerId);
        }
      } catch {
        // 忽略释放指针捕获异常
      }
    }
    pendingId = null;
    activePointerId = null;
    captureEl = null;
    startedDrag = false;
    draggingId.value = null;
    dragOverId.value = null;
    dragDeltaX.value = 0;
    dragDeltaY.value = 0;
  }

  function handleWindowPointerMove(event: PointerEvent): void {
    if (activePointerId === null || event.pointerId !== activePointerId || pendingId === null) {
      return;
    }
    lastClientX = event.clientX;
    lastClientY = event.clientY;
    const dx = event.clientX - startClientX;
    const dy = event.clientY - startClientY;

    if (!startedDrag) {
      if (Math.hypot(dx, dy) < 4) {
        return;
      }
      startedDrag = true;
      draggingId.value = pendingId;
      document.body.classList.add("is-pointer-dragging");
      clearSelection();
      if (scrollRafId === null) {
        scrollRafId = window.requestAnimationFrame(stepEdgeAutoScroll);
      }
    }

    clearSelection();
    dragDeltaX.value = dx;
    dragDeltaY.value = dy;
    updateDropTarget(event.clientX, event.clientY);
  }

  function handleWindowPointerUp(event: PointerEvent): void {
    if (activePointerId === null || event.pointerId !== activePointerId) {
      return;
    }
    const source = draggingId.value;
    const target = dragOverId.value;
    const pos = dragOverPosition.value;
    const didDrag = startedDrag;
    cleanupListeners();
    clearSelection();
    if (didDrag && source !== null && target !== null && source !== target) {
      options.onReorder(source, target, pos);
    }
  }

  function handleWindowPointerCancel(event: PointerEvent): void {
    if (activePointerId !== null && event.pointerId !== activePointerId) {
      return;
    }
    cleanupListeners();
    clearSelection();
  }

  function handleWindowKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && (pendingId !== null || draggingId.value !== null)) {
      cleanupListeners();
      clearSelection();
    }
  }

  function startDrag(
    id: T,
    event: PointerEvent,
    cfg: { fromHandle?: boolean } = {}
  ): void {
    if (event.button !== 0 && event.pointerType === "mouse") {
      return;
    }
    const fromHandle = Boolean(cfg.fromHandle);
    // 卡片标题栏空白区仅在鼠标指针下允许触发拖动，移动端触控滑动仍保持自然页面滚动
    if (!fromHandle) {
      if (event.pointerType !== "mouse") {
        return;
      }
      const targetEl = event.target as HTMLElement | null;
      if (targetEl?.closest("button, a, input, select, textarea")) {
        return;
      }
    }

    event.preventDefault();
    clearSelection();

    pendingId = id;
    activePointerId = event.pointerId;
    startClientX = event.clientX;
    startClientY = event.clientY;
    lastClientX = event.clientX;
    lastClientY = event.clientY;
    startedDrag = false;
    dragDeltaX.value = 0;
    dragDeltaY.value = 0;

    const currentTarget = event.currentTarget as HTMLElement | null;
    if (fromHandle && currentTarget?.setPointerCapture) {
      try {
        currentTarget.setPointerCapture(event.pointerId);
        captureEl = currentTarget;
      } catch {
        captureEl = null;
      }
    }

    window.addEventListener("pointermove", handleWindowPointerMove, { passive: true });
    window.addEventListener("pointerup", handleWindowPointerUp);
    window.addEventListener("pointercancel", handleWindowPointerCancel);
    window.addEventListener("keydown", handleWindowKeydown);
  }

  function getCardDragStyle(id: T): Record<string, string> {
    if (draggingId.value !== id) {
      return {};
    }
    return {
      transform: `translate3d(${dragDeltaX.value}px, ${dragDeltaY.value}px, 0) scale(1.015)`,
    };
  }

  onUnmounted(() => {
    cleanupListeners();
  });

  return {
    draggingId,
    dragOverId,
    dragOverPosition,
    dragDeltaX,
    dragDeltaY,
    startDrag,
    getCardDragStyle,
  };
}
