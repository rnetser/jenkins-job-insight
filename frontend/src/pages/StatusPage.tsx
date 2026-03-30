import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '@/lib/api'
import { formatTimestamp, isAnalysisTimeout, INVALID_DATE_FALLBACK } from '@/lib/utils'
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
  completed: {
    title: 'Analysis complete',
    subtitle: 'Analysis finished.',
  },
}

/** Title text for terminal error states rendered via the error branch. */
const terminalErrorTitles: Record<string, string> = {
  not_found: 'Job not found',
  unauthorized: 'Access denied',
  failed: 'Analysis failed',
}

export function StatusPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<ResultResponse | null>(null)
  const [error, setError] = useState('')
  const [terminalErrorKind, setTerminalErrorKind] = useState<'not_found' | 'unauthorized' | 'failed' | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval>>(null)

  useEffect(() => {
    if (!jobId) return

    let cancelled = false
    let inFlight = false
    const stopPolling = () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
    setData(null)
    setError('')
    setTerminalErrorKind(null)

    async function poll() {
      if (inFlight || cancelled) return
      inFlight = true
      try {
        const res = await api.get<ResultResponse>(`/results/${jobId}`)
        if (cancelled) return
        setError('')
        setData(res)
        if (res.status === 'completed') {
          stopPolling()
          navigate(`/results/${jobId}`, { replace: true })
        } else if (res.status === 'failed') {
          stopPolling()
          setTerminalErrorKind('failed')
          setError(res.result?.error ?? 'Analysis failed')
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError && (err.status === 404 || err.status === 403)) {
            // Permanent error — stop polling
            stopPolling()
            setTerminalErrorKind(err.status === 404 ? 'not_found' : 'unauthorized')
            setError(
              err.status === 404
                ? 'Job not found. It may have been deleted.'
                : 'Access denied. You are not authorized to view this job.'
            )
            setData(null)
          } else {
            // Transient transport error — keep polling, don't clear data
            setTerminalErrorKind(null)
            setError('Failed to reach the server. Retrying...')
          }
        }
      } finally {
        inFlight = false
      }
    }

    poll()
    intervalRef.current = setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      stopPolling()
    }
  }, [jobId, navigate])

  const status = data?.status ?? terminalErrorKind ?? 'pending'
  const isTimeout = isAnalysisTimeout(status, error)
  const displayStatus = isTimeout ? 'timeout' : status
  const queuedAtDisplay = data?.created_at ? formatTimestamp(data.created_at) : null
  const isRunning = displayStatus === 'running'
  const isWaiting = displayStatus === 'waiting'
  const isActive = isRunning || isWaiting
  const msg = statusMessages[displayStatus] ?? statusMessages.running
  const statusBadgeLabel = displayStatus.replace(/_/g, ' ').toUpperCase()

  return (
    <div className="relative flex min-h-screen items-start justify-center overflow-x-hidden overflow-y-auto bg-surface-page py-8 sm:items-center">
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
            <div className="text-center" aria-live="polite" aria-atomic="true">
              {error && terminalErrorKind ? (
                isTimeout ? (
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
                      {terminalErrorTitles[terminalErrorKind] ?? terminalErrorTitles.failed}
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
                    data?.jenkins_url ? (
                      <a href={String(data.jenkins_url)} target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline font-mono">
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
                    {statusBadgeLabel}
                  </Badge>
                }
              />
              {queuedAtDisplay && queuedAtDisplay !== INVALID_DATE_FALLBACK && (
                <Row
                  label="QUEUED"
                  value={queuedAtDisplay}
                />
              )}
            </div>

            {error && !terminalErrorKind && (
              <p
                role="status"
                aria-live="polite"
                aria-atomic="true"
                className="text-xs text-signal-orange animate-fade-in"
              >
                {error}
              </p>
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
  value: ReactNode
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
