import { useNavigate } from 'react-router-dom'
import { getUsername, clearUsername } from '@/lib/cookies'
import { LogOut } from 'lucide-react'

export function UserBadge() {
  const username = getUsername()
  const navigate = useNavigate()

  if (!username) return null

  function handleLogout() {
    clearUsername()
    navigate('/register')
  }

  return (
    <div className="flex items-center gap-2 rounded-full bg-surface-elevated px-3 py-1 text-sm text-text-secondary">
      <div className="h-2 w-2 rounded-full bg-signal-green" />
      <span className="font-mono text-xs">{username}</span>
      <button
        type="button"
        aria-label="Logout"
        onClick={handleLogout}
        className="ml-1 rounded-sm p-0.5 text-text-tertiary transition-colors hover:text-signal-red"
        title="Logout"
      >
        <LogOut className="h-3 w-3" />
      </button>
    </div>
  )
}
