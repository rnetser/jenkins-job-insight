import { Navigate } from 'react-router-dom'
import { isLoggedIn } from '@/lib/cookies'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  if (!isLoggedIn()) {
    return <Navigate to="/register" replace />
  }
  return <>{children}</>
}
