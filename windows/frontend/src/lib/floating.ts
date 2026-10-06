// Like RequestImage's native dialog, popovers use the browser's top layer so
// table cells and scrolling cards cannot clip them. Theme inheritance is kept.
export function popover(node: HTMLElement, open = true) {
  function update(visible: boolean) {
    if (!node.isConnected || node.matches(':popover-open') === visible) return;
    if (visible) node.showPopover(); else node.hidePopover();
  }
  update(open);
  return {update, destroy: () => update(false)};
}

export function viewportBounds() {
  const viewport = window.visualViewport;
  const left = viewport?.offsetLeft ?? 0, top = viewport?.offsetTop ?? 0;
  const width = Math.min(window.innerWidth, document.documentElement.clientWidth || window.innerWidth, viewport?.width ?? window.innerWidth);
  const height = Math.min(window.innerHeight, document.documentElement.clientHeight || window.innerHeight, viewport?.height ?? window.innerHeight);
  return {left, top, width, height, right: left + width, bottom: top + height};
}
