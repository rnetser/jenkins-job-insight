import { Navigate } from 'react-router-dom'
import { isLoggedIn } from '@/lib/cookies'
import { useAuth } from '@/lib/auth'

interface Props {
  children: React.ReactNode
  adminOnly?: boolean
}

export function ProtectedRoute({ children, adminOnly }: Props) {
  const { isAdmin, loading } = useAuth()

  if (!isLoggedIn()) {
    return <Navigate to="/register" replace />
  }

  if (adminOnly) {
    if (loading) return null  // Prevent flash while checking admin status
    if (!isAdmin) return <Navigate to="/" replace />
  }

  return <>{children}</>
}
