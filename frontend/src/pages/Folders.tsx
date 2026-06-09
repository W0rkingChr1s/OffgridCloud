import { useEffect, useState } from "react";
import { api, ApiError, type Folder, type User } from "../api";
import Layout from "../components/Layout";

export default function Folders() {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  function load() {
    api<Folder[]>("/api/folders").then(setFolders).catch(reportError);
    api<User[]>("/api/users").then(setUsers).catch(reportError);
  }
  function reportError(e: unknown) {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }
  useEffect(load, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api<Folder>("/api/folders", {
        method: "POST",
        body: JSON.stringify({ name, description }),
      });
      setName("");
      setDescription("");
      load();
    } catch (e) {
      reportError(e);
    }
  }

  async function toggleAccess(folder: Folder, userId: number) {
    const next = folder.user_ids.includes(userId)
      ? folder.user_ids.filter((id) => id !== userId)
      : [...folder.user_ids, userId];
    try {
      await api<Folder>(`/api/folders/${folder.id}/access`, {
        method: "PUT",
        body: JSON.stringify({ user_ids: next }),
      });
      load();
    } catch (e) {
      reportError(e);
    }
  }

  async function remove(folder: Folder) {
    if (!confirm(`Ordner „${folder.name}“ samt Dateien löschen?`)) return;
    try {
      await api(`/api/folders/${folder.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      reportError(e);
    }
  }

  const field =
    "rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal";

  return (
    <Layout>
      <h2 className="mb-1 text-2xl font-bold">Ordner verwalten</h2>
      <p className="mb-6 text-sm text-slate-400">
        Ordner anlegen und Benutzern Upload-Zugriff freigeben.
      </p>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
      )}

      <form onSubmit={create} className="mb-8 grid gap-3 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10 sm:grid-cols-[1fr_2fr_auto]">
        <input className={field} placeholder="Ordnername" value={name} onChange={(e) => setName(e.target.value)} required />
        <input className={field} placeholder="Beschreibung (optional)" value={description} onChange={(e) => setDescription(e.target.value)} />
        <button type="submit" className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 font-semibold text-white">
          Anlegen
        </button>
      </form>

      <div className="space-y-4">
        {folders.map((f) => (
          <div key={f.id} className="rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-lg font-semibold text-white">{f.name}</div>
                {f.description && <div className="text-sm text-slate-400">{f.description}</div>}
                <div className="mt-1 text-xs text-slate-500">{f.media_count} Dateien</div>
              </div>
              <button
                onClick={() => remove(f)}
                className="rounded border border-red-500/30 px-2 py-1 text-xs text-red-300 hover:bg-red-500/10"
              >
                Löschen
              </button>
            </div>

            <div className="mt-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Freigegeben für
              </div>
              <div className="flex flex-wrap gap-2">
                {users.filter((u) => u.role !== "admin").length === 0 && (
                  <span className="text-sm text-slate-500">Keine (Nicht-Admin-)Benutzer vorhanden.</span>
                )}
                {users
                  .filter((u) => u.role !== "admin")
                  .map((u) => {
                    const on = f.user_ids.includes(u.id);
                    return (
                      <button
                        key={u.id}
                        onClick={() => toggleAccess(f, u.id)}
                        className={`rounded-full px-3 py-1 text-sm transition ${
                          on
                            ? "bg-ogc-teal/20 text-ogc-teal ring-1 ring-ogc-teal/40"
                            : "bg-slate-700/50 text-slate-400 hover:text-white"
                        }`}
                      >
                        {on ? "✓ " : ""}
                        {u.name || u.email}
                      </button>
                    );
                  })}
              </div>
            </div>
          </div>
        ))}
      </div>
    </Layout>
  );
}
