// Keep keyboard focus inside dialogs and restore it to the opening control.
let openDialogs = 0;
let previousOverflow = "";
export default {
  mounted(el, binding) {
    const previous = document.activeElement;
    if (openDialogs++ === 0) {
      previousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    const focusable = () =>
      [
        ...el.querySelectorAll(
          'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
        ),
      ].filter((node) => node.getClientRects().length);
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        binding.value?.();
        return;
      }
      if (event.key !== "Tab") return;
      const nodes = focusable(),
        first = nodes[0],
        last = nodes.at(-1);
      if (!first) {
        event.preventDefault();
        el.focus();
        return;
      }
      if (
        event.shiftKey &&
        (document.activeElement === first || document.activeElement === el)
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last ||
          !el.contains(document.activeElement))
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    el.tabIndex = -1;
    el.addEventListener("keydown", onKey);
    queueMicrotask(() => (focusable()[0] || el).focus());
    el._cleanupFocus = () => {
      el.removeEventListener("keydown", onKey);
      if (--openDialogs === 0) document.body.style.overflow = previousOverflow;
      if (previous?.isConnected) previous.focus();
    };
  },
  unmounted(el) {
    el._cleanupFocus?.();
  },
};
