import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, type SetupStatus } from "./api";
import { AuthProvider } from "./auth";
import ConnectionBanner from "./components/ConnectionBanner";
import ProtectedRoute from "./components/ProtectedRoute";
import { RetroEasterEgg } from "./retro";
import { ToastProvider } from "./toast";
import TransferToaster from "./TransferToaster";
import Login from "./pages/Login";

// Every page except the login is its own chunk: the first visit (often over a
// weak field uplink) only fetches the shell, admin pages load when opened.
const Bandwidth = lazy(() => import("./pages/Bandwidth"));
const Bluetooth = lazy(() => import("./pages/Bluetooth"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const FolderDetail = lazy(() => import("./pages/FolderDetail"));
const Folders = lazy(() => import("./pages/Folders"));
const Groups = lazy(() => import("./pages/Groups"));
const Health = lazy(() => import("./pages/Health"));
const Network = lazy(() => import("./pages/Network"));
const Overview = lazy(() => import("./pages/Overview"));
const Passkeys = lazy(() => import("./pages/Passkeys"));
const Pool = lazy(() => import("./pages/Pool"));
const Providers = lazy(() => import("./pages/Providers"));
const Search = lazy(() => import("./pages/Search"));
const Setup = lazy(() => import("./pages/Setup"));
const Storage = lazy(() => import("./pages/Storage"));
const System = lazy(() => import("./pages/System"));
const Transfers = lazy(() => import("./pages/Transfers"));
const Users = lazy(() => import("./pages/Users"));
const Vpn = lazy(() => import("./pages/Vpn"));

function PageFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 text-slate-400">
      Lädt…
    </div>
  );
}

// A box flashed from the prebuilt image starts without any account: until the
// setup wizard has run, it replaces the whole app (every other API answers 503).
function SetupGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  useEffect(() => {
    api<SetupStatus>("/api/setup/status")
      .then(setStatus)
      .catch(() => setStatus({ required: false, code_required: false, suggested_hostname: "", country: "" }));
  }, []);
  if (!status) return null;
  if (status.required)
    return (
      <Suspense fallback={<PageFallback />}>
        <Setup status={status} />
      </Suspense>
    );
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <SetupGate>
      <BrowserRouter>
        <RetroEasterEgg />
        <AuthProvider>
          <ToastProvider>
            <TransferToaster />
            <Suspense fallback={<PageFallback />}>
            <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Overview />
                </ProtectedRoute>
              }
            />
            <Route
              path="/folders"
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              }
            />
            <Route
              path="/folders/:id"
              element={
                <ProtectedRoute>
                  <FolderDetail />
                </ProtectedRoute>
              }
            />
            <Route
              path="/search"
              element={
                <ProtectedRoute>
                  <Search />
                </ProtectedRoute>
              }
            />
            <Route
              path="/passkeys"
              element={
                <ProtectedRoute>
                  <Passkeys />
                </ProtectedRoute>
              }
            />
            <Route
              path="/bluetooth"
              element={
                <ProtectedRoute>
                  <Bluetooth />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/folders"
              element={
                <ProtectedRoute requireAdmin>
                  <Folders />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/providers"
              element={
                <ProtectedRoute requireAdmin>
                  <Providers />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/vpn"
              element={
                <ProtectedRoute requireAdmin>
                  <Vpn />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/transfers"
              element={
                <ProtectedRoute requireAdmin>
                  <Transfers />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/bandwidth"
              element={
                <ProtectedRoute requireAdmin>
                  <Bandwidth />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/network"
              element={
                <ProtectedRoute requireAdmin>
                  <Network />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/pool"
              element={
                <ProtectedRoute requireAdmin>
                  <Pool />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/storage"
              element={
                <ProtectedRoute requireAdmin>
                  <Storage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/health"
              element={
                <ProtectedRoute requireAdmin>
                  <Health />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/system"
              element={
                <ProtectedRoute requireAdmin>
                  <System />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/groups"
              element={
                <ProtectedRoute requireAdmin>
                  <Groups />
                </ProtectedRoute>
              }
            />
            <Route
              path="/users"
              element={
                <ProtectedRoute requireAdmin>
                  <Users />
                </ProtectedRoute>
              }
            />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
            </Suspense>
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </SetupGate>
  );
}

export default function App() {
  return (
    <>
      <ConnectionBanner />
      <AppRoutes />
    </>
  );
}
