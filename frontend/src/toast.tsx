import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ToastVariant = "success" | "error" | "info" | "warning";

export interface Toast {
  id: number;
  variant: ToastVariant;
  title: string;
  message?: string;
}

interface ToastApi {
  push: (t: Omit<Toast, "id">) => number;
  dismiss: (id: number) => void;
  success: (title: string, message?: string) => number;
  error: (title: string, message?: string) => number;
  info: (title: string, message?: string) => number;
  warning: (title: string, message?: string) => number;
}

const ToastContext = createContext<ToastApi | undefined>(undefined);

const AUTO_DISMISS_MS = 5000;
const MAX_VISIBLE = 5;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((ts) => ts.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = (seq.current += 1);
    // Newest on top; cap the stack so a burst can't fill the screen.
    setToasts((ts) => [{ ...t, id }, ...ts].slice(0, MAX_VISIBLE));
    return id;
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      push,
      dismiss,
      success: (title, message) => push({ variant: "success", title, message }),
      error: (title, message) => push({ variant: "error", title, message }),
      info: (title, message) => push({ variant: "info", title, message }),
      warning: (title, message) => push({ variant: "warning", title, message }),
    }),
    [push, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx;
}

const VARIANTS: Record<ToastVariant, { ring: string; bar: string; icon: string }> = {
  success: { ring: "ring-emerald-400/30", bar: "bg-emerald-400", icon: "✓" },
  error: { ring: "ring-red-400/30", bar: "bg-red-400", icon: "✕" },
  info: { ring: "ring-ogc-teal/30", bar: "bg-ogc-teal", icon: "ℹ" },
  warning: { ring: "ring-amber-400/30", bar: "bg-amber-400", icon: "⚠" },
};

function ToastViewport({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed inset-x-0 top-0 z-50 flex flex-col items-center gap-2 px-3 pt-3 sm:inset-x-auto sm:right-4 sm:items-end"
    >
      {toasts.map((t) => (
        <ToastCard key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastCard({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const v = VARIANTS[toast.variant];
  useEffect(() => {
    const timer = window.setTimeout(() => onDismiss(toast.id), AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [toast.id, onDismiss]);

  return (
    <div
      role="status"
      className={`pointer-events-auto flex w-full max-w-sm items-start gap-3 overflow-hidden rounded-xl bg-slate-800/95 px-4 py-3 text-sm shadow-lg ring-1 ${v.ring} backdrop-blur`}
    >
      <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs text-slate-900 ${v.bar}`}>
        {v.icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-medium text-white">{toast.title}</div>
        {toast.message && <div className="mt-0.5 break-words text-xs text-slate-300">{toast.message}</div>}
      </div>
      <button
        onClick={() => onDismiss(toast.id)}
        aria-label="Schließen"
        className="-mr-1 shrink-0 rounded p-0.5 text-slate-400 hover:text-white"
      >
        ✕
      </button>
    </div>
  );
}
