import { useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, type SetupStatus } from "./api";
import { AuthProvider } from "./auth";
import ConnectionBanner from "./components/ConnectionBanner";
import ProtectedRoute from "./components/ProtectedRoute";
import { RetroEasterEgg } from "./retro";
import { ToastProvider } from "./toast";
import TransferToaster from "./TransferToaster";
import Bandwidth from "./pages/Bandwidth";
import Bluetooth from "./pages/Bluetooth";
import Dashboard from "./pages/Dashboard";
import FolderDetail from "./pages/FolderDetail";
import Folders from "./pages/Folders";
import Groups from "./pages/Groups";
import Login from "./pages/Login";
import Network from "./pages/Network";
import Overview from "./pages/Overview";
import Passkeys from "./pages/Passkeys";
import Pool from "./pages/Pool";
import Providers from "./pages/Providers";
import Search from "./pages/Search";
import Setup from "./pages/Setup";
import Storage from "./pages/Storage";
import System from "./pages/System";
import Transfers from "./pages/Transfers";
import Users from "./pages/Users";
import Vpn from "./pages/Vpn";

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
  if (status.required) return <Setup status={status} />;
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
