import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth";

function NavLink({ to, label }: { to: string; label: string }) {
  const { pathname } = useLocation();
  const active = pathname === to;
  return (
    <Link
      to={to}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
        active ? "bg-white/10 text-white" : "text-slate-400 hover:text-white"
      }`}
    >
      {label}
    </Link>
  );
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-900 to-ogc-indigo/40 text-slate-100">
      <header className="border-b border-white/5">
        <div className="mx-auto flex max-w-5xl items-center gap-4 px-6 py-4">
          <Link to="/" className="flex items-center gap-2">
            <img src="/logo-icon.svg" alt="OffgridCloud" className="h-9 w-9" />
            <span className="text-lg font-bold">OffgridCloud</span>
          </Link>
          <nav className="flex items-center gap-1">
            <NavLink to="/" label="Ordner" />
            {user?.role === "admin" && <NavLink to="/admin/folders" label="Verwalten" />}
            {user?.role === "admin" && <NavLink to="/admin/providers" label="Provider" />}
            {user?.role === "admin" && <NavLink to="/admin/transfers" label="Transfers" />}
            {user?.role === "admin" && <NavLink to="/users" label="Benutzer" />}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-sm text-slate-400">
              {user?.name || user?.email}
              {user?.role === "admin" && (
                <span className="ml-2 rounded bg-ogc-teal/20 px-1.5 py-0.5 text-xs text-ogc-teal">
                  Admin
                </span>
              )}
            </span>
            <button
              onClick={logout}
              className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-slate-300 hover:bg-white/5"
            >
              Abmelden
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
    </div>
  );
}
