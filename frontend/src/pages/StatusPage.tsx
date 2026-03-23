import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '@/lib/api'
import type { ResultResponse } from '@/types'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

const POLL_MS = 10_000

export function StatusPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<ResultResponse | null>(null)
  const [error, setError] = useState('')
  const intervalRef = useRef<ReturnType<typeof setInterval>>(null)
  const inFlightRef = useRef(false)

  useEffect(() => {
    if (!jobId) return

    async function poll() {
      if (inFlightRef.current) return
      inFlightRef.current = true
      try {
        setError('')
        const res = await api.get<ResultResponse>(`/results/${jobId}`)
        setData(res)
        if (res.status === 'completed') {
          if (intervalRef.current) clearInterval(intervalRef.current)
          navigate(`/results/${jobId}`, { replace: true })
        } else if (res.status === 'failed') {
          if (intervalRef.current) clearInterval(intervalRef.current)
          setError(res.result ? String((res.result as any).error || 'Analysis failed') : 'Analysis failed')
        }
      } catch {
        setError('Failed to reach the server. Retrying...')
      } finally {
        inFlightRef.current = false
      }
    }

    poll()
    intervalRef.current = setInterval(poll, POLL_MS)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [jobId, navigate])

  const status = data?.status ?? 'pending'
  const isRunning = status === 'running'

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-surface-page overflow-hidden">
      {/* Scan-line overlay */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.015]"
        style={{
          backgroundImage:
            'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(56,139,253,.3) 2px, rgba(56,139,253,.3) 4px)',
        }}
      />

      <div className="relative z-10 w-full max-w-xl px-4">
        <Card className="animate-slide-up border-border-muted">
          <CardContent className="flex flex-col items-center gap-6 p-8">
            {/* Pulsing / spinning indicator */}
            <div className="relative flex h-24 w-24 items-center justify-center">
              {/* Outer ring */}
              <svg className="absolute inset-0 h-full w-full" viewBox="0 0 96 96">
                {/* Track */}
                <circle
                  cx="48" cy="48" r="42"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1"
                  className="text-border-muted"
                />
                {/* Active arc */}
                <circle
                  cx="48" cy="48" r="42"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeDasharray={isRunning ? '80 184' : '264 0'}
                  className={`text-signal-blue ${isRunning ? 'animate-spin-slow' : 'animate-pulse-ring'}`}
                  style={{ transformOrigin: 'center' }}
                />
              </svg>

              {/* Center dot */}
              <div
                className={`h-3 w-3 rounded-full bg-signal-blue ${isRunning ? '' : 'animate-pulse-ring'}`}
              />

              {/* Ambient glow */}
              <div className="pointer-events-none absolute inset-0 rounded-full bg-signal-blue/[0.06] blur-xl" />
            </div>

            {/* Status label */}
            <div className="text-center">
              {error && status === 'failed' ? (
                <>
                  <h2 className="font-display text-lg font-semibold text-signal-red">
                    Analysis failed
                  </h2>
                  <p className="mt-2 text-sm text-signal-red/80 bg-signal-red/10 rounded-md px-3 py-2">
                    {error}
                  </p>
                </>
              ) : (
                <>
                  <h2 className="font-display text-lg font-semibold text-text-primary">
                    {isRunning ? 'Analysis in progress' : 'Waiting for analysis'}
                  </h2>
                  <p className="mt-1 text-sm text-text-tertiary">
                    {isRunning
                      ? 'Crunching test results with AI...'
                      : 'Job is queued. This page refreshes automatically.'}
                  </p>
                </>
              )}
            </div>

            {/* Metadata rows */}
            <div className="w-full space-y-2 rounded-md border border-border-muted bg-surface-elevated/50 p-4 text-sm">
              <Row label="JOB ID" value={jobId ?? '—'} mono />
              {data?.result?.job_name && (
                <Row label="JOB" value={data.result.job_name} mono />
              )}
              {data?.result?.build_number != null && (
                <Row label="BUILD" value={`#${data.result.build_number}`} mono />
              )}
              <Row
                label="STATUS"
                value={
                  <Badge variant={isRunning ? 'default' : 'outline'}>
                    {status.toUpperCase()}
                  </Badge>
                }
              />
              {data?.created_at && (
                <Row
                  label="QUEUED"
                  value={new Date(data.created_at).toLocaleString()}
                />
              )}
            </div>

            {error && (
              <p className="text-xs text-signal-orange animate-fade-in">{error}</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function Row({
  label,
  value,
  mono,
}: {
  label: string
  value: React.ReactNode
  mono?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="whitespace-nowrap font-display text-[10px] font-medium uppercase tracking-widest text-text-tertiary">
        {label}
      </span>
      <span
        className={`text-right text-text-secondary break-all ${mono ? 'font-mono text-xs' : ''}`}
      >
        {value}
      </span>
    </div>
  )
}
