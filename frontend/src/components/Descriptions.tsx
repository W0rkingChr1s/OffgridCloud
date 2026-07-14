import { useMemo, useState } from "react";
import {
  api,
  ApiError,
  type DescriptionDeleteResult,
  getToken,
  type MediaDescription,
} from "../api";

function sidecarDownloadUrl(id: number): string {
  return `/api/media/${id}/download?token=${encodeURIComponent(getToken() ?? "")}`;
}

/**
 * Create/edit dialog for a thematic description. On save it persists via the
 * descriptions API and hands the fresh record back so the parent can refresh.
 *
 * In *create* mode the description covers ``mediaIds`` (e.g. the just-uploaded
 * batch or the current selection). In *edit* mode the covered set is left as-is
 * and only the title/body change.
 */
export function DescriptionModal({
  folderId,
  existing = null,
  mediaIds = [],
  filenames = {},
  onClose,
  onSaved,
}: {
  folderId: number;
  existing?: MediaDescription | null;
  mediaIds?: number[];
  filenames?: Record<number, string>;
  onClose: () => void;
  onSaved: (desc: MediaDescription) => void;
}) {
  const [title, setTitle] = useState(existing?.title ?? "");
  const [body, setBody] = useState(existing?.body ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const coveredIds = existing ? existing.media_ids : mediaIds;
  const coveredNames = useMemo(
    () => coveredIds.map((id) => filenames[id]).filter(Boolean),
    [coveredIds, filenames],
  );

  async function save() {
    const trimmed = body.trim();
    if (!trimmed) {
      setError("Bitte eine Beschreibung eingeben.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = existing
        ? await api<MediaDescription>(`/api/descriptions/${existing.id}`, {
            method: "PATCH",
            body: JSON.stringify({ title: title.trim(), body: trimmed }),
          })
        : await api<MediaDescription>(`/api/folders/${folderId}/descriptions`, {
            method: "POST",
            body: JSON.stringify({ title: title.trim(), body: trimmed, media_ids: coveredIds }),
          });
      onSaved(saved);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-2xl bg-slate-900 p-5 ring-1 ring-white/10"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-white">
          {existing ? "Beschreibung bearbeiten" : "Fotos & Videos beschreiben"}
        </h3>
        <p className="mt-1 text-xs text-slate-400">
          Daraus wird eine Textdatei erzeugt und zusammen mit den Medien in die Cloud geladen.
        </p>

        <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-slate-400">
          Thema (optional)
        </label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={200}
          placeholder="z. B. Bootsfahrt am Morgen"
          className="mt-1 w-full rounded-lg bg-slate-800 px-3 py-2 text-sm text-white outline-none ring-1 ring-white/10 focus:ring-ogc-teal/50"
        />

        <label className="mt-3 block text-xs font-medium uppercase tracking-wide text-slate-400">
          Beschreibung
        </label>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={5}
          maxLength={20000}
          autoFocus
          placeholder="Was ist auf den Aufnahmen zu sehen?"
          className="mt-1 w-full resize-y rounded-lg bg-slate-800 px-3 py-2 text-sm text-white outline-none ring-1 ring-white/10 focus:ring-ogc-teal/50"
        />

        {coveredNames.length > 0 && (
          <div className="mt-3 text-xs text-slate-400">
            <span className="font-medium text-slate-300">
              {coveredNames.length} Datei(en):
            </span>{" "}
            {coveredNames.slice(0, 6).join(", ")}
            {coveredNames.length > 6 && ` +${coveredNames.length - 6} weitere`}
          </div>
        )}

        {error && <div className="mt-3 text-sm text-red-300">{error}</div>}

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={busy}
            className="rounded-lg px-3 py-1.5 text-sm text-slate-300 hover:bg-white/5 disabled:opacity-50"
          >
            Abbrechen
          </button>
          <button
            onClick={save}
            disabled={busy}
            className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "Speichere…" : "Speichern"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Read-only card list of a folder's descriptions with edit/delete controls. */
export function DescriptionsList({
  descriptions,
  filenames,
  onEdit,
  onDeleted,
}: {
  descriptions: MediaDescription[];
  filenames: Record<number, string>;
  onEdit: (desc: MediaDescription) => void;
  onDeleted: (id: number) => void;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);

  async function remove(desc: MediaDescription) {
    if (!window.confirm("Beschreibung (inkl. Textdatei) wirklich löschen?")) return;
    setBusyId(desc.id);
    try {
      await api<DescriptionDeleteResult>(`/api/descriptions/${desc.id}`, { method: "DELETE" });
      onDeleted(desc.id);
    } catch {
      /* surfaced by parent reload */
    } finally {
      setBusyId(null);
    }
  }

  if (descriptions.length === 0) return null;

  return (
    <div className="mb-6">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
        Beschreibungen &amp; Themen ({descriptions.length})
      </h3>
      <div className="space-y-2">
        {descriptions.map((d) => {
          const names = d.media_ids.map((id) => filenames[id]).filter(Boolean);
          return (
            <div key={d.id} className="rounded-xl bg-slate-800/60 p-3 ring-1 ring-white/5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  {d.title && <div className="font-medium text-white">{d.title}</div>}
                  <p className="mt-0.5 whitespace-pre-wrap text-sm text-slate-300">{d.body}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2 text-xs">
                  <button
                    onClick={() => onEdit(d)}
                    className="rounded border border-white/10 px-2 py-1 text-slate-200 hover:bg-white/5"
                  >
                    Bearbeiten
                  </button>
                  <button
                    onClick={() => remove(d)}
                    disabled={busyId === d.id}
                    className="rounded border border-red-500/30 px-2 py-1 text-red-300 hover:bg-red-500/10 disabled:opacity-50"
                  >
                    {busyId === d.id ? "…" : "Löschen"}
                  </button>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
                {names.length > 0 && (
                  <span>
                    {names.length} Datei(en): {names.slice(0, 4).join(", ")}
                    {names.length > 4 && ` +${names.length - 4}`}
                  </span>
                )}
                {d.txt_media_id !== null && (
                  <a
                    href={sidecarDownloadUrl(d.txt_media_id)}
                    download
                    className="text-ogc-teal hover:underline"
                  >
                    📄 {d.txt_filename}
                  </a>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
