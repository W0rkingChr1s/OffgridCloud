import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth";
import ProtectedRoute from "./components/ProtectedRoute";
import Dashboard from "./pages/Dashboard";
import FolderDetail from "./pages/FolderDetail";
import Folders from "./pages/Folders";
import Login from "./pages/Login";
import Providers from "./pages/Providers";
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
