import { useEffect, useRef, type ReactNode } from "react";

// Plain conditionally-rendered <dialog open>, not showModal(): jsdom does not
// implement showModal, and this keeps behavior fully driven by React state
// (open/close never desyncs from a native imperative call). We still provide
// the two keyboard requirements from docs/interface-v1.md ("Interface and
// taste" -> Keyboard): a focus trap while open, and Escape dismissal.
export function Modal({
  open,
  onClose,
  labelledBy,
  children,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (!open) return;
    const node = ref.current;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusable = () =>
      node?.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      ) ?? [];

    const preferred = node?.querySelector<HTMLElement>("[data-initial-focus]");
    (preferred ?? focusable()[0])?.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const items = Array.from(focusable());
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      previouslyFocused?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div
        style={{ position: "fixed", inset: 0, background: "#0009", zIndex: 10 }}
        onClick={onClose}
        aria-hidden="true"
      />
      <dialog
        ref={ref}
        open
        aria-modal="true"
        aria-labelledby={labelledBy}
        style={{ position: "fixed", top: "8vh", left: "50%", transform: "translateX(-50%)", zIndex: 11, margin: 0 }}
      >
        {children}
      </dialog>
    </>
  );
}
