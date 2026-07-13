import { useEffect, useState } from "react";
import { api, ApiError, type Role, type User } from "../api";
import { useAuth } from "../auth";
import Layout from "../components/Layout";
import { SortTh, type SortOption, useSort } from "../components/Sort";

const USER_SORT: SortOption<User>[] = [
  { key: "email", label: "E-Mail", get: (u) => u.email },
  { key: "name", label: "Name", get: (u) => u.name },
  { key: "role", label: "Rolle", get: (u) => u.role },
  { key: "status", label: "Status", get: (u) => (u.active ? 0 : 1) },
];

export default function Users() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  function load() {
    api<User[]>("/api/users")
      .then(setUsers)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Fehler"));
  }

  useEffect(load, []);

  async function patch(id: number, body: Record<string, unknown>) {
    setError(null);
    try {
      await api<User>(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Fehler");
    }
  }

  async function remove(id: number) {
    if (!confirm("Benutzer wirklich löschen?")) return;
    setError(null);
    try {
      await api(`/api/users/${id}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Fehler");
    }
  }

  async function resetPassword(id: number) {
    const pw = prompt("Neues Passwort (mind. 8 Zeichen):");
    if (!pw) return;
    await patch(id, { password: pw });
  }

  const sort = useSort(users, USER_SORT, { key: "email" });

  return (
    <Layout>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Benutzerverwaltung</h2>
          <p className="text-sm text-slate-400">Konten anlegen, sperren, Rollen ändern.</p>
        </div>
        <button
          onClick={() => setShowCreate((v) => !v)}
          className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue px-4 py-2 text-sm font-semibold text-white"
        >
          {showCreate ? "Abbrechen" : "+ Benutzer"}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-500/15 px-3 py-2 text-sm text-red-300">{error}</div>
      )}

      {showCreate && (
        <CreateUserForm
          onCreated={() => {
            setShowCreate(false);
            load();
          }}
          onError={setError}
        />
      )}

      <div className="overflow-hidden rounded-2xl ring-1 ring-white/10">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-800/80 text-slate-400">
            <tr>
              <SortTh sort={sort} field="email">E-Mail</SortTh>
              <SortTh sort={sort} field="name">Name</SortTh>
              <SortTh sort={sort} field="role">Rolle</SortTh>
              <SortTh sort={sort} field="status">Status</SortTh>
              <th className="px-4 py-3 text-right">Aktionen</th>
            </tr>
          </thead>
          <tbody>
            {sort.sorted.map((u) => {
              const self = u.id === me?.id;
              return (
                <tr key={u.id} className="border-t border-white/5 bg-slate-900/40">
                  <td className="px-4 py-3 font-medium text-white">{u.email}</td>
                  <td className="px-4 py-3 text-slate-300">{u.name || "—"}</td>
                  <td className="px-4 py-3">
                    <select
                      value={u.role}
                      disabled={self}
                      onChange={(e) => patch(u.id, { role: e.target.value as Role })}
                      className="rounded border border-white/10 bg-slate-800 px-2 py-1 disabled:opacity-50"
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded px-2 py-0.5 text-xs ${
                        u.active ? "bg-emerald-500/20 text-emerald-300" : "bg-slate-500/20 text-slate-400"
                      }`}
                    >
                      {u.active ? "aktiv" : "gesperrt"}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => patch(u.id, { active: !u.active })}
                        disabled={self}
                        className="rounded border border-white/10 px-2 py-1 text-xs hover:bg-white/5 disabled:opacity-30"
                      >
                        {u.active ? "Sperren" : "Entsperren"}
                      </button>
                      <button
                        onClick={() => resetPassword(u.id)}
                        className="rounded border border-white/10 px-2 py-1 text-xs hover:bg-white/5"
                      >
                        Passwort
                      </button>
                      <button
                        onClick={() => remove(u.id)}
                        disabled={self}
                        className="rounded border border-red-500/30 px-2 py-1 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-30"
                      >
                        Löschen
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Layout>
  );
}

function CreateUserForm({
  onCreated,
  onError,
}: {
  onCreated: () => void;
  onError: (msg: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("user");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api<User>("/api/users", {
        method: "POST",
        body: JSON.stringify({ email, name, password, role }),
      });
      onCreated();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Anlegen fehlgeschlagen");
    }
  }

  const field = "rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 outline-none focus:border-ogc-teal";

  return (
    <form onSubmit={submit} className="mb-6 grid gap-3 rounded-2xl bg-slate-800/60 p-5 ring-1 ring-white/10 sm:grid-cols-2">
      <input className={field} placeholder="E-Mail (z. B. user@offgrid.local)" value={email} onChange={(e) => setEmail(e.target.value)} required />
      <input className={field} placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
      <input className={field} type="password" placeholder="Passwort (mind. 8 Zeichen)" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
      <select className={field} value={role} onChange={(e) => setRole(e.target.value as Role)}>
        <option value="user">user</option>
        <option value="admin">admin</option>
      </select>
      <button type="submit" className="rounded-lg bg-gradient-to-r from-ogc-teal to-ogc-blue py-2 font-semibold text-white sm:col-span-2">
        Anlegen
      </button>
    </form>
  );
}
