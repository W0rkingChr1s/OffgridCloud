import { useEffect, useState } from "react";
import { api, ApiError, type Group, type User } from "../api";
import Layout from "../components/Layout";

export default function Groups() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  function report(e: unknown) {
    setError(e instanceof ApiError ? e.message : "Fehler");
  }
  function load() {
    api<Group[]>("/api/groups").then(setGroups).catch(report);
    api<User[]>("/api/users").then(setUsers).catch(report);
  }
  useEffect(load, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api<Group>("/api/groups", { method: "POST", body: JSON.stringify({ name, description }) });
      setName("");
      setDescription("");
      load();
    } catch (e) {
      report(e);
    }
  }

  async function toggleMember(group: Group, userId: number) {
    const next = group.member_ids.includes(userId)
      ? group.member_ids.filter((id) => id !== userId)
      : [...group.member_ids, userId];
    try {
      await api(`/api/groups/${group.id}/members`, { method: "PUT", body: JSON.stringify({ user_ids: next }) });
      load();
    } catch (e) {
      report(e);
    }
  }

  async function remove(group: Group) {
    if (!confirm(`Gruppe „${group.name}“ löschen?`)) return;
    try {
      await api(`/api/groups/${group.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      report(e);
    }
  }

  const field = "rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal";
  const members = users.filter((u) => u.role !== "admin");

  return (
    <Layout>
      <h2 className="mb-1 text-2xl font-bold">Teams / Gruppen</h2>
      <p className="mb-6 text-sm text-slate-400">
        Benutzer zu Teams bündeln und Ordner an ganze Teams freigeben.
      </p>

      {error && <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>}

      <form onSubmit={create} className="mb-8 grid gap-3 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10 sm:grid-cols-[1fr_2fr_auto]">
        <input className={field} placeholder="Teamname" value={name} onChange={(e) => setName(e.target.value)} required />
        <input className={field} placeholder="Beschreibung (optional)" value={description} onChange={(e) => setDescription(e.target.value)} />
        <button type="submit" className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 font-semibold text-white">
          Anlegen
        </button>
      </form>

      <div className="space-y-4">
        {groups.length === 0 && <p className="text-sm text-slate-500">Noch keine Teams.</p>}
        {groups.map((g) => (
          <div key={g.id} className="rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-lg font-semibold text-white">{g.name}</div>
                {g.description && <div className="text-sm text-slate-400">{g.description}</div>}
                <div className="mt-1 text-xs text-slate-500">{g.member_ids.length} Mitglieder</div>
              </div>
              <button onClick={() => remove(g)} className="rounded border border-red-500/30 px-2 py-1 text-xs text-red-300 hover:bg-red-500/10">
                Löschen
              </button>
            </div>
            <div className="mt-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Mitglieder</div>
              <div className="flex flex-wrap gap-2">
                {members.length === 0 && <span className="text-sm text-slate-500">Keine Benutzer vorhanden.</span>}
                {members.map((u) => {
                  const on = g.member_ids.includes(u.id);
                  return (
                    <button
                      key={u.id}
                      onClick={() => toggleMember(g, u.id)}
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
            </div>
          </div>
        ))}
      </div>
    </Layout>
  );
}
