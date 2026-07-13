import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  type Folder,
  type FolderProviderLink,
  type Group,
  type Provider,
  type User,
} from "../api";
import Layout from "../components/Layout";
import { SortMenu, type SortOption, useSort } from "../components/Sort";

const FOLDER_SORT: SortOption<Folder>[] = [
  { key: "name", label: "Name", get: (f) => f.name },
  { key: "media", label: "Dateien", get: (f) => f.media_count },
  { key: "created", label: "Erstellt", get: (f) => f.created_at },
];

export default function Folders() {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [links, setLinks] = useState<Record<number, FolderProviderLink[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  function report(e: unknown) {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }

  async function load() {
    try {
      const fs = await api<Folder[]>("/api/folders");
      setFolders(fs);
      api<User[]>("/api/users").then(setUsers).catch(report);
      api<Group[]>("/api/groups").then(setGroups).catch(report);
      api<Provider[]>("/api/providers").then(setProviders).catch(report);
      const entries = await Promise.all(
        fs.map(async (f) => [f.id, await api<FolderProviderLink[]>(`/api/folders/${f.id}/providers`)] as const),
      );
      setLinks(Object.fromEntries(entries));
    } catch (e) {
      report(e);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api<Folder>("/api/folders", { method: "POST", body: JSON.stringify({ name, description }) });
      setName("");
      setDescription("");
      load();
    } catch (e) {
      report(e);
    }
  }

  async function toggleAccess(folder: Folder, userId: number) {
    const next = folder.user_ids.includes(userId)
      ? folder.user_ids.filter((id) => id !== userId)
      : [...folder.user_ids, userId];
    try {
      await api(`/api/folders/${folder.id}/access`, { method: "PUT", body: JSON.stringify({ user_ids: next }) });
      load();
    } catch (e) {
      report(e);
    }
  }

  async function removeFolder(folder: Folder) {
    if (!confirm(`Ordner „${folder.name}“ samt Dateien löschen?`)) return;
    try {
      await api(`/api/folders/${folder.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      report(e);
    }
  }

  async function toggleGroupAccess(folder: Folder, groupId: number) {
    const next = folder.group_ids.includes(groupId)
      ? folder.group_ids.filter((id) => id !== groupId)
      : [...folder.group_ids, groupId];
    try {
      await api(`/api/folders/${folder.id}/groups`, { method: "PUT", body: JSON.stringify({ group_ids: next }) });
      load();
    } catch (e) {
      report(e);
    }
  }

  async function addLink(folderId: number, providerId: number, destPath: string, priority: number) {
    try {
      await api(`/api/folders/${folderId}/providers`, {
        method: "POST",
        body: JSON.stringify({ provider_id: providerId, dest_path: destPath, priority }),
      });
      load();
    } catch (e) {
      report(e);
    }
  }

  async function removeLink(folderId: number, providerId: number) {
    try {
      await api(`/api/folders/${folderId}/providers/${providerId}`, { method: "DELETE" });
      load();
    } catch (e) {
      report(e);
    }
  }

  const field = "rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal";

  const sort = useSort(folders, FOLDER_SORT, { key: "name" });

  return (
    <Layout>
      <h2 className="mb-1 text-2xl font-bold">Ordner verwalten</h2>
      <p className="mb-6 text-sm text-slate-400">
        Ordner anlegen, Benutzern freigeben und mit Cloud-Zielen verknüpfen.
      </p>

      {error && <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      <form onSubmit={create} className="mb-8 grid gap-3 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10 sm:grid-cols-[1fr_2fr_auto]">
        <input className={field} placeholder="Ordnername" value={name} onChange={(e) => setName(e.target.value)} required />
        <input className={field} placeholder="Beschreibung (optional)" value={description} onChange={(e) => setDescription(e.target.value)} />
        <button type="submit" className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 font-semibold text-white">
          Anlegen
        </button>
      </form>

      {folders.length > 0 && (
        <div className="mb-4 flex justify-end">
          <SortMenu sort={sort} />
        </div>
      )}

      <div className="space-y-4">
        {sort.sorted.map((f) => (
          <div key={f.id} className="rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-lg font-semibold text-white">{f.name}</div>
                {f.description && <div className="text-sm text-slate-400">{f.description}</div>}
                <div className="mt-1 text-xs text-slate-500">{f.media_count} Dateien</div>
              </div>
              <button onClick={() => removeFolder(f)} className="rounded border border-red-500/30 px-2 py-1 text-xs text-red-300 hover:bg-red-500/10">
                Löschen
              </button>
            </div>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Freigegeben für (Benutzer)</div>
                <div className="mb-3 flex flex-wrap gap-2">
                  {users.filter((u) => u.role !== "admin").length === 0 && (
                    <span className="text-sm text-slate-500">Keine Benutzer vorhanden.</span>
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
                            on ? "bg-ogc-teal/20 text-ogc-teal ring-1 ring-ogc-teal/40" : "bg-slate-700/50 text-slate-400 hover:text-white"
                          }`}
                        >
                          {on ? "✓ " : ""}
                          {u.name || u.email}
                        </button>
                      );
                    })}
                </div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Teams</div>
                <div className="flex flex-wrap gap-2">
                  {groups.length === 0 && <span className="text-sm text-slate-500">Keine Teams angelegt.</span>}
                  {groups.map((g) => {
                    const on = f.group_ids.includes(g.id);
                    return (
                      <button
                        key={g.id}
                        onClick={() => toggleGroupAccess(f, g.id)}
                        className={`rounded-full px-3 py-1 text-sm transition ${
                          on ? "bg-ogc-blue/20 text-ogc-blue ring-1 ring-ogc-blue/40" : "bg-slate-700/50 text-slate-400 hover:text-white"
                        }`}
                      >
                        {on ? "✓ " : ""}⛂ {g.name}
                      </button>
                    );
                  })}
                </div>
              </div>

              <ProviderLinks
                folderId={f.id}
                links={links[f.id] ?? []}
                providers={providers}
                onAdd={addLink}
                onRemove={removeLink}
              />
            </div>
          </div>
        ))}
      </div>
    </Layout>
  );
}

function ProviderLinks({
  folderId,
  links,
  providers,
  onAdd,
  onRemove,
}: {
  folderId: number;
  links: FolderProviderLink[];
  providers: Provider[];
  onAdd: (folderId: number, providerId: number, destPath: string, priority: number) => void;
  onRemove: (folderId: number, providerId: number) => void;
}) {
  const linkedIds = new Set(links.map((l) => l.provider_id));
  const available = providers.filter((p) => !linkedIds.has(p.id));
  const [providerId, setProviderId] = useState<number | "">("");
  const [destPath, setDestPath] = useState("");
  const [priority, setPriority] = useState(0);

  const field = "rounded-lg border border-white/10 bg-slate-900/60 px-2 py-1.5 text-sm outline-none focus:border-ogc-teal";

  return (
    <div>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Cloud-Ziele</div>
      <div className="space-y-1">
        {links.length === 0 && <div className="text-sm text-slate-500">Keine Ziele verknüpft.</div>}
        {links.map((l) => (
          <div key={l.id} className="flex items-center justify-between rounded-lg bg-slate-900/40 px-3 py-1.5 text-sm">
            <span className="text-slate-200">
              {l.provider_name}
              {l.dest_path && <span className="text-slate-500"> → {l.dest_path}</span>}
              {l.priority !== 0 && <span className="ml-1 text-xs text-ogc-teal">P{l.priority}</span>}
            </span>
            <button onClick={() => onRemove(folderId, l.provider_id)} className="text-xs text-red-300 hover:underline">
              entfernen
            </button>
          </div>
        ))}
      </div>

      {available.length > 0 && (
        <div className="mt-2 flex gap-2">
          <select className={field} value={providerId} onChange={(e) => setProviderId(Number(e.target.value))}>
            <option value="">Provider…</option>
            {available.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <input className={`${field} flex-1`} placeholder="Ziel-Pfad / Bucket (optional)" value={destPath} onChange={(e) => setDestPath(e.target.value)} />
          <input className={`${field} w-20`} type="number" title="Priorität (höher = zuerst)" value={priority} onChange={(e) => setPriority(Number(e.target.value))} />
          <button
            disabled={!providerId}
            onClick={() => {
              if (providerId) {
                onAdd(folderId, providerId, destPath, priority);
                setProviderId("");
                setDestPath("");
                setPriority(0);
              }
            }}
            className="rounded-lg bg-ogc-blue/80 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            +
          </button>
        </div>
      )}
    </div>
  );
}
