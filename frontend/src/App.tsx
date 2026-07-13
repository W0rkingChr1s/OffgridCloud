import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth";
import ProtectedRoute from "./components/ProtectedRoute";
import { RetroEasterEgg } from "./retro";
import Bandwidth from "./pages/Bandwidth";
import Dashboard from "./pages/Dashboard";
import FolderDetail from "./pages/FolderDetail";
import Folders from "./pages/Folders";
import Groups from "./pages/Groups";
import Login from "./pages/Login";
import Network from "./pages/Network";
import Pool from "./pages/Pool";
import Providers from "./pages/Providers";
import Search from "./pages/Search";
import System from "./pages/System";
import Transfers from "./pages/Transfers";
import Users from "./pages/Users";
import Vpn from "./pages/Vpn";

export default function App() {
  return (
    <BrowserRouter>
      <RetroEasterEgg />
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
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
      </AuthProvider>
    </BrowserRouter>
  );
}
