import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { setUsername } from '@/lib/cookies'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

export function RegisterPage() {
  const [value, setValue] = useState('')
  const navigate = useNavigate()

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = value.trim()
    if (!trimmed) return
    setUsername(trimmed)
    navigate('/')
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-surface-page overflow-hidden">
      {/* Ambient grid */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage:
            'linear-gradient(rgba(56,139,253,.4) 1px, transparent 1px), linear-gradient(90deg, rgba(56,139,253,.4) 1px, transparent 1px)',
          backgroundSize: '48px 48px',
        }}
      />

      {/* Radial glow behind card */}
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[600px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-signal-blue/[0.04] blur-3xl" />

      <div className="relative z-10 w-full max-w-sm px-4">
        {/* Logo / Title block */}
        <div className="mb-8 animate-slide-up text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-lg border border-border-default bg-surface-card">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" className="text-signal-blue">
              <path
                d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <h1 className="font-display text-xl font-bold tracking-tight text-text-primary">
            Jenkins Job Insight
          </h1>
          <p className="mt-1 text-sm text-text-tertiary">
            Identify yourself to continue
          </p>
        </div>

        {/* Card */}
        <Card className="animate-slide-up border-border-muted [animation-delay:80ms] [animation-fill-mode:backwards]">
          <CardContent className="p-5">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <label
                  htmlFor="username"
                  className="block font-display text-xs font-medium uppercase tracking-widest text-text-secondary"
                >
                  Callsign
                </label>
                <Input
                  id="username"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  placeholder="e.g. jdoe"
                  autoFocus
                  autoComplete="username"
                  className="h-10 font-mono"
                />
              </div>
              <Button type="submit" className="w-full" disabled={!value.trim()}>
                Enter Command Deck
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* Subtle footer */}
        <p className="mt-6 animate-slide-up text-center text-xs text-text-tertiary [animation-delay:160ms] [animation-fill-mode:backwards]">
          Your callsign is stored as a browser cookie.
          <br />
          No account or password required.
        </p>
      </div>
    </div>
  )
}
