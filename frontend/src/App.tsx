import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth";
import ProtectedRoute from "./components/ProtectedRoute";
import Bandwidth from "./pages/Bandwidth";
import Dashboard from "./pages/Dashboard";
import FolderDetail from "./pages/FolderDetail";
import Folders from "./pages/Folders";
import Login from "./pages/Login";
import Providers from "./pages/Providers";
import System from "./pages/System";
import Transfers from "./pages/Transfers";
import Users from "./pages/Users";

export default function App() {
  return (
    <BrowserRouter>
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
            path="/admin/system"
            element={
              <ProtectedRoute requireAdmin>
                <System />
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
