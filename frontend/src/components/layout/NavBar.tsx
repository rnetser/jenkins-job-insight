import { Link, useLocation } from 'react-router-dom'
import { UserBadge } from './UserBadge'
import { cn } from '@/lib/utils'

const NAV_LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/history', label: 'History' },
]

export function NavBar() {
  const location = useLocation()

  return (
    <header className="sticky top-0 z-50 border-b border-border-default bg-surface-card/95 backdrop-blur-sm">
      <div className="mx-auto flex h-14 max-w-[1400px] items-center justify-between px-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-6">
          <Link
            to="/"
            className="font-display text-lg font-bold tracking-tight text-text-primary"
          >
            JJI
          </Link>
          <nav className="flex items-center gap-1">
            {NAV_LINKS.map(({ to, label }) => (
              <Link
                key={to}
                to={to}
                className={cn(
                  'rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150',
                  (location.pathname === to || (to !== '/' && location.pathname.startsWith(to)))
                    ? 'bg-surface-elevated text-text-primary'
                    : 'text-text-secondary hover:bg-surface-hover hover:text-text-primary',
                )}
              >
                {label}
              </Link>
            ))}
          </nav>
        </div>
        <UserBadge />
      </div>
    </header>
  )
}
