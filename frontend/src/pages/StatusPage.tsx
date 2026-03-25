import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '@/lib/api'
import { parseApiTimestamp, isAnalysisTimeout } from '@/lib/utils'
import type { ResultResponse } from '@/types'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Clock, Loader2 } from 'lucide-react'

const POLL_MS = 10_000

const statusMessages: Record<string, { title: string; subtitle: string }> = {
  waiting: {
    title: 'Waiting for Jenkins job',
    subtitle: 'Monitoring build until it completes...',
  },
  pending: {
    title: 'Analysis queued',
    subtitle: 'Waiting in the analysis queue...',
  },
  running: {
    title: 'Analysis in progress',
    subtitle: 'Crunching test results with AI...',
  },
}

export function StatusPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<ResultResponse | null>(null)
  const [error, setError] = useState('')
  const intervalRef = useRef<ReturnType<typeof setInterval>>(null)
  const inFlightRef = useRef(false)

  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    if (!jobId) return

    async function poll() {
      if (inFlightRef.current) return
      inFlightRef.current = true
      try {
        setError('')
        const res = await api.get<ResultResponse>(`/results/${jobId}`)
        if (!mountedRef.current) return
        setData(res)
        if (res.status === 'completed') {
          if (intervalRef.current) clearInterval(intervalRef.current)
          navigate(`/results/${jobId}`, { replace: true })
        } else if (res.status === 'failed') {
          if (intervalRef.current) clearInterval(intervalRef.current)
          setError(res.result?.error ?? 'Analysis failed')
        }
      } catch {
        if (mountedRef.current) {
          setError('Failed to reach the server. Retrying...')
        }
      } finally {
        inFlightRef.current = false
      }
    }

    poll()
    intervalRef.current = setInterval(poll, POLL_MS)
    return () => {
      mountedRef.current = false
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [jobId, navigate])

  const status = data?.status ?? 'pending'
  const isRunning = status === 'running'
  const isWaiting = status === 'waiting'
  const isActive = isRunning || isWaiting
  const msg = statusMessages[status] ?? statusMessages.running

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
                  strokeDasharray={isActive ? '80 184' : '264 0'}
                  className={`text-signal-blue ${isActive ? 'animate-spin-slow' : 'animate-pulse-ring'}`}
                  style={{ transformOrigin: 'center' }}
                />
              </svg>

              {/* Center icon: Clock for waiting, spinner for running, dot for pending */}
              {isWaiting ? (
                <Clock className="h-6 w-6 text-signal-blue animate-pulse-ring" />
              ) : isRunning ? (
                <Loader2 className="h-6 w-6 text-signal-blue animate-spin" />
              ) : (
                <div className="h-3 w-3 rounded-full bg-signal-blue animate-pulse-ring" />
              )}

              {/* Ambient glow */}
              <div className="pointer-events-none absolute inset-0 rounded-full bg-signal-blue/[0.06] blur-xl" />
            </div>

            {/* Status label */}
            <div className="text-center">
              {error && status === 'failed' ? (
                isAnalysisTimeout(status, error) ? (
                  <>
                    <div className="flex items-center justify-center gap-2">
                      <Clock className="h-5 w-5 text-signal-orange" />
                      <h2 className="font-display text-lg font-semibold text-signal-orange">
                        AI Analysis Timed Out
                      </h2>
                    </div>
                    <p className="mt-2 text-sm text-signal-orange/80 bg-signal-orange/10 rounded-md px-3 py-2">
                      {error}
                    </p>
                  </>
                ) : (
                  <>
                    <h2 className="font-display text-lg font-semibold text-signal-red">
                      Analysis failed
                    </h2>
                    <p className="mt-2 text-sm text-signal-red/80 bg-signal-red/10 rounded-md px-3 py-2">
                      {error}
                    </p>
                  </>
                )
              ) : (
                <>
                  <h2 className="font-display text-lg font-semibold text-text-primary">
                    {msg.title}
                  </h2>
                  <p className="mt-1 text-sm text-text-tertiary">
                    {msg.subtitle}
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
                <Row
                  label="BUILD"
                  value={
                    data?.result?.jenkins_url ? (
                      <a href={String(data.result.jenkins_url)} target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline font-mono">
                        #{data.result.build_number}
                      </a>
                    ) : (
                      `#${data.result.build_number}`
                    )
                  }
                  mono
                />
              )}
              <Row
                label="STATUS"
                value={
                  <Badge variant={isRunning || isWaiting ? 'default' : 'outline'}>
                    {status.toUpperCase()}
                  </Badge>
                }
              />
              {data?.created_at && (
                <Row
                  label="QUEUED"
                  value={parseApiTimestamp(data.created_at).toLocaleString()}
                />
              )}
            </div>

            {error && status !== 'failed' && (
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
