import { useState } from "react";
import { api } from "../api";

/**
 * Inline tag chips with add/remove. Persists the full set via
 * ``PUT /api/media/{id}/tags`` on every change and reports it back up.
 */
export function TagEditor({
  mediaId,
  tags,
  editable = true,
  onChange,
}: {
  mediaId: number;
  tags: string[];
  editable?: boolean;
  onChange?: (tags: string[]) => void;
}) {
  const [items, setItems] = useState<string[]>(tags);
  const [adding, setAdding] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(next: string[]) {
    setBusy(true);
    try {
      const saved = await api<string[]>(`/api/media/${mediaId}/tags`, {
        method: "PUT",
        body: JSON.stringify({ tags: next }),
      });
      setItems(saved);
      onChange?.(saved);
    } catch {
      /* leave the chips as they were */
    } finally {
      setBusy(false);
    }
  }

  function commit() {
    const tag = value.trim().toLowerCase();
    setValue("");
    setAdding(false);
    if (tag && !items.includes(tag)) save([...items, tag]);
  }

  return (
    <div className="flex flex-wrap items-center gap-1">
      {items.map((t) => (
        <span
          key={t}
          className="inline-flex items-center gap-1 rounded-full bg-ogc-indigo/40 px-2 py-0.5 text-xs text-slate-200"
        >
          #{t}
          {editable && (
            <button
              type="button"
              onClick={() => save(items.filter((x) => x !== t))}
              disabled={busy}
              aria-label={`Tag ${t} entfernen`}
              className="text-slate-400 hover:text-white disabled:opacity-50"
            >
              ×
            </button>
          )}
        </span>
      ))}
      {items.length === 0 && !editable && <span className="text-xs text-slate-500">—</span>}
      {editable &&
        (adding ? (
          <input
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") {
                setAdding(false);
                setValue("");
              }
            }}
            placeholder="Tag"
            maxLength={64}
            className="w-24 rounded bg-slate-700 px-2 py-0.5 text-xs text-white outline-none ring-1 ring-ogc-teal/40"
          />
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={() => setAdding(true)}
            className="rounded-full border border-white/15 px-2 py-0.5 text-xs text-slate-400 hover:border-white/30 hover:text-white disabled:opacity-50"
          >
            + Tag
          </button>
        ))}
    </div>
  );
}
