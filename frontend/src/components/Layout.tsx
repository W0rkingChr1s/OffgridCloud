import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth";

type IconProps = { className?: string };

const icons = {
  folders: (p: IconProps) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
    </svg>
  ),
  manage: (p: IconProps) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
      <path d="M12 11v5M9.5 13.5h5" />
    </svg>
  ),
  provider: (p: IconProps) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M7 18a4 4 0 0 1-.5-7.97A5.5 5.5 0 0 1 17.5 9.5 3.75 3.75 0 0 1 17 18H7Z" />
    </svg>
  ),
  transfers: (p: IconProps) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M7 7h11l-3-3M17 17H6l3 3" />
    </svg>
  ),
  bandwidth: (p: IconProps) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M4 18v-4M9 18V9M14 18v-7M19 18V6" />
    </svg>
  ),
  users: (p: IconProps) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 19a5.5 5.5 0 0 1 11 0M16 6.5a3 3 0 0 1 0 5.9M17.5 19a5.5 5.5 0 0 0-2-4.2" />
    </svg>
  ),
  teams: (p: IconProps) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <circle cx="7" cy="8" r="2.5" />
      <circle cx="17" cy="8" r="2.5" />
      <path d="M2.5 18a4.5 4.5 0 0 1 9 0M12.5 18a4.5 4.5 0 0 1 9 0" />
    </svg>
  ),
  system: (p: IconProps) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M18.4 5.6l-1.8 1.8M7.4 16.6l-1.8 1.8" />
    </svg>
  ),
  vpn: (p: IconProps) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3Z" />
      <path d="M9.5 12l1.8 1.8 3.2-3.6" />
    </svg>
  ),
};

type NavItem = { to: string; label: string; icon: (p: IconProps) => JSX.Element; admin?: boolean };

const NAV: NavItem[] = [
  { to: "/", label: "Ordner", icon: icons.folders },
  { to: "/admin/folders", label: "Verwalten", icon: icons.manage, admin: true },
  { to: "/admin/providers", label: "Provider", icon: icons.provider, admin: true },
  { to: "/admin/vpn", label: "VPN", icon: icons.vpn, admin: true },
  { to: "/admin/transfers", label: "Transfers", icon: icons.transfers, admin: true },
  { to: "/admin/bandwidth", label: "Bandbreite", icon: icons.bandwidth, admin: true },
  { to: "/users", label: "Benutzer", icon: icons.users, admin: true },
  { to: "/admin/groups", label: "Teams", icon: icons.teams, admin: true },
  { to: "/admin/system", label: "System", icon: icons.system, admin: true },
];

function DesktopNavLink({ to, label }: { to: string; label: string }) {
  const { pathname } = useLocation();
  const active = pathname === to;
  return (
    <Link
      to={to}
      className={`shrink-0 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition ${
        active ? "bg-white/10 text-white" : "text-slate-400 hover:text-white"
      }`}
    >
      {label}
    </Link>
  );
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);

  const items = NAV.filter((n) => !n.admin || user?.role === "admin");

  // Close the drawer whenever we navigate.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Lock body scroll while the drawer is open.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-900 to-ogc-indigo/40 text-slate-100">
      <header className="sticky top-0 z-30 border-b border-white/5 bg-slate-900/70 backdrop-blur-md pt-[env(safe-area-inset-top)]">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3 sm:px-6 sm:py-4">
          <Link to="/" className="flex shrink-0 items-center gap-2">
            <img src="/logo-icon.svg" alt="OffgridCloud" className="h-9 w-9 shrink-0" />
            <span className="whitespace-nowrap text-lg font-bold">OffgridCloud</span>
          </Link>

          {/* Desktop navigation */}
          <nav className="hidden min-w-0 items-center gap-1 overflow-x-auto lg:flex">
            {items.map((n) => (
              <DesktopNavLink key={n.to} to={n.to} label={n.label} />
            ))}
          </nav>

          <div className="ml-auto hidden shrink-0 items-center gap-3 lg:flex">
            <span className="whitespace-nowrap text-sm text-slate-400">
              {user?.name || user?.email}
              {user?.role === "admin" && (
                <span className="ml-2 rounded bg-ogc-teal/20 px-1.5 py-0.5 text-xs text-ogc-teal">Admin</span>
              )}
            </span>
            <button
              onClick={logout}
              className="shrink-0 rounded-lg border border-white/10 px-3 py-1.5 text-sm text-slate-300 hover:bg-white/5"
            >
              Abmelden
            </button>
          </div>

          {/* Mobile menu button */}
          <button
            onClick={() => setOpen(true)}
            aria-label="Menü öffnen"
            aria-expanded={open}
            className="ml-auto flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-white/10 text-slate-200 active:bg-white/10 lg:hidden"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>
        </div>
      </header>

      {/* Mobile drawer */}
      <div className={`fixed inset-0 z-50 lg:hidden ${open ? "" : "pointer-events-none"}`} aria-hidden={!open}>
        <div
          onClick={() => setOpen(false)}
          className={`absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity duration-200 ${
            open ? "opacity-100" : "opacity-0"
          }`}
        />
        <div
          role="dialog"
          aria-modal="true"
          className={`absolute right-0 top-0 flex h-full w-[82%] max-w-xs flex-col border-l border-white/10 bg-slate-900 shadow-2xl transition-transform duration-300 ease-out ${
            open ? "translate-x-0" : "translate-x-full"
          }`}
          style={{ paddingTop: "env(safe-area-inset-top)", paddingBottom: "env(safe-area-inset-bottom)" }}
        >
          <div className="flex items-center justify-between px-5 py-4">
            <div className="flex items-center gap-2">
              <img src="/logo-icon.svg" alt="" className="h-8 w-8" />
              <span className="text-base font-bold">OffgridCloud</span>
            </div>
            <button
              onClick={() => setOpen(false)}
              aria-label="Menü schließen"
              className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-300 active:bg-white/10"
            >
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>

          <div className="mx-5 mb-2 flex items-center gap-3 rounded-xl bg-white/5 px-4 py-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-ogc-teal to-ogc-blue text-sm font-bold text-white">
              {(user?.name || user?.email || "?").charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-white">{user?.name || user?.email}</div>
              {user?.role === "admin" ? (
                <span className="text-xs text-ogc-teal">Administrator</span>
              ) : (
                <span className="text-xs text-slate-500">{user?.email}</span>
              )}
            </div>
          </div>

          <nav className="flex-1 overflow-y-auto px-3 py-2">
            {items.map((n) => {
              const active = pathname === n.to;
              const Icon = n.icon;
              return (
                <Link
                  key={n.to}
                  to={n.to}
                  className={`mb-1 flex items-center gap-3 rounded-xl px-4 py-3 text-[15px] font-medium transition ${
                    active ? "bg-ogc-teal/15 text-white ring-1 ring-ogc-teal/30" : "text-slate-300 active:bg-white/5"
                  }`}
                >
                  <Icon className={`h-5 w-5 shrink-0 ${active ? "text-ogc-teal" : "text-slate-400"}`} />
                  {n.label}
                </Link>
              );
            })}
          </nav>

          <div className="border-t border-white/5 px-3 py-3">
            <button
              onClick={logout}
              className="flex w-full items-center justify-center gap-2 rounded-xl border border-white/10 px-4 py-3 text-sm font-medium text-slate-200 active:bg-white/5"
            >
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M15 12H4M11 8l-4 4 4 4M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
              </svg>
              Abmelden
            </button>
          </div>
        </div>
      </div>

      <main className="mx-auto max-w-6xl px-4 py-6 pb-[calc(env(safe-area-inset-bottom)+1.5rem)] sm:px-6 sm:py-8">
        {children}
      </main>
    </div>
  );
}
