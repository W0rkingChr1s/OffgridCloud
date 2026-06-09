import { Navigate } from "react-router-dom";
import { useAuth } from "../auth";

export default function ProtectedRoute({
  children,
  requireAdmin = false,
}: {
  children: React.ReactNode;
  requireAdmin?: boolean;
}) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-900 text-slate-400">
        Lädt…
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  if (requireAdmin && user.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
