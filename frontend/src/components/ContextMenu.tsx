import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MoreMenuItem } from "./MoreMenu";

/** A menu entry; falsy entries are dropped so callers can write `cond && {...}`. */
export type ContextMenuEntry = MoreMenuItem | "separator" | false | null | undefined;

type Open = { x: number; y: number; items: (MoreMenuItem | "separator")[] };

/** Drop falsy entries plus leading, trailing and doubled separators. */
function tidy(entries: ContextMenuEntry[]): (MoreMenuItem | "separator")[] {
  const out: (MoreMenuItem | "separator")[] = [];
  for (const e of entries) {
    if (!e) continue;
    if (e === "separator" && (out.length === 0 || out[out.length - 1] === "separator")) continue;
    out.push(e);
  }
  if (out[out.length - 1] === "separator") out.pop();
  return out;
}

/** Places where the browser's own menu is the better one (paste, copy, spell check). */
function wantsNativeMenu(e: React.MouseEvent): boolean {
  if (e.shiftKey) return true;
  const t = e.target as HTMLElement;
  if (t.closest("input, textarea, select, [contenteditable='true']")) return true;
  const sel = window.getSelection();
  return !!sel && !sel.isCollapsed && sel.toString().trim() !== "";
}

/**
 * Right-click menu for rows and cards. It carries the same actions as the
 * visible buttons, so it's a shortcut on desktop, never the only way in.
 * Shift + right-click, form fields and selected text keep the browser's menu.
 *
 *   const ctx = useContextMenu();
 *   <tr onContextMenu={ctx.bind(() => [...items])}>…</tr>
 *   {ctx.menu}
 */
export function useContextMenu() {
  const [open, setOpen] = useState<Open | null>(null);
  const close = useCallback(() => setOpen(null), []);

  const bind = useCallback(
    (items: () => ContextMenuEntry[]) => (e: React.MouseEvent) => {
      if (wantsNativeMenu(e)) return;
      const list = tidy(items());
      if (!list.some((i) => i !== "separator")) return;
      e.preventDefault();
      let { clientX: x, clientY: y } = e;
      // Opened from the keyboard (menu key, Shift+F10): anchor on the element.
      if (x === 0 && y === 0) {
        const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
        x = r.left + 16;
        y = r.top + r.height / 2;
      }
      setOpen({ x, y, items: list });
    },
    [],
  );

  const menu = open ? <Menu {...open} onClose={close} /> : null;
  return { bind, menu, close };
}

function Menu({ x, y, items, onClose }: Open & { onClose: () => void }): ReactNode {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: x, top: y, ready: false });
  // Where focus was before the menu took it; given back when closed by keyboard.
  const prevFocus = useRef(document.activeElement as HTMLElement | null);

  // Measure once rendered, then keep it inside the viewport (flip at the edges).
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    const pad = 8;
    let left = x;
    let top = y;
    if (left + width > window.innerWidth - pad) left = Math.max(pad, x - width);
    if (top + height > window.innerHeight - pad) top = Math.max(pad, y - height);
    setPos({ left, top, ready: true });
  }, [x, y]);

  // Focus the first usable entry so arrows/Enter work right away (only once
  // visible: a visibility:hidden element can't take focus).
  useEffect(() => {
    if (pos.ready) ref.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
  }, [pos.ready]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    // A fixed menu would drift away from what was clicked, so just close it.
    const closeNow = () => onClose();
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", closeNow, true);
    window.addEventListener("resize", closeNow);
    window.addEventListener("blur", closeNow);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", closeNow, true);
      window.removeEventListener("resize", closeNow);
      window.removeEventListener("blur", closeNow);
    };
  }, [onClose]);

  function onKeyDown(e: React.KeyboardEvent) {
    const buttons = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
    const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const move = (to: number) => buttons[(to + buttons.length) % buttons.length]?.focus();
    if (e.key === "Escape" || e.key === "Tab") {
      e.preventDefault();
      onClose();
      prevFocus.current?.focus?.({ preventScroll: true });
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      move(i + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      move(i < 0 ? -1 : i - 1);
    } else if (e.key === "Home") {
      e.preventDefault();
      move(0);
    } else if (e.key === "End") {
      e.preventDefault();
      move(-1);
    }
  }

  // Portaled to <body>: an animated (transformed) ancestor would otherwise
  // make position:fixed relative to itself instead of the viewport.
  return createPortal(
    <div
      ref={ref}
      role="menu"
      onKeyDown={onKeyDown}
      onContextMenu={(e) => e.preventDefault()}
      style={{ left: pos.left, top: pos.top, visibility: pos.ready ? "visible" : "hidden" }}
      className="fixed z-50 min-w-52 max-w-80 overflow-hidden rounded-xl bg-slate-900 py-1 shadow-xl ring-1 ring-white/10"
    >
      {items.map((it, idx) =>
        it === "separator" ? (
          <div key={`sep-${idx}`} role="separator" className="my-1 border-t border-white/10" />
        ) : (
          <button
            key={it.label}
            type="button"
            role="menuitem"
            disabled={it.disabled}
            title={it.title}
            onClick={() => {
              onClose();
              it.onClick();
            }}
            className={`block w-full truncate px-3 py-2 text-left text-sm outline-hidden hover:bg-white/5 focus:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40 ${
              it.danger ? "text-red-300" : "text-slate-200"
            }`}
          >
            {it.label}
          </button>
        ),
      )}
    </div>,
    document.body,
  );
}
