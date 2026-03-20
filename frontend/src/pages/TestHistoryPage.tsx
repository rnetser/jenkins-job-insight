import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '@/lib/api'
import type { TestHistory } from '@/types'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ClassificationBadge } from '@/components/shared/ClassificationBadge'
import { Skeleton } from '@/components/ui/skeleton'

export function TestHistoryPage() {
  const { testName } = useParams<{ testName: string }>()
  const decoded = testName ? decodeURIComponent(testName) : ''
  const [data, setData] = useState<TestHistory | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!decoded) return
    setLoading(true)
    api.get<TestHistory>(`/history/test/${encodeURIComponent(decoded)}`)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [decoded])

  if (loading) return <div className="space-y-4"><Skeleton className="h-8 w-96" /><Skeleton className="h-48 w-full" /></div>
  if (error) return <p className="text-signal-red text-sm py-10 text-center">{error}</p>
  if (!data) return null

  const rate = data.failure_rate !== null ? Math.round(data.failure_rate * 100) : null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-lg font-bold text-text-primary break-all">{decoded}</h1>
        {data.note && <p className="mt-1 text-xs text-text-tertiary">{data.note}</p>}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Failure Rate" value={rate !== null ? `${rate}%` : 'N/A'} color={rate === null ? 'text-text-tertiary' : rate > 50 ? 'text-signal-red' : rate > 20 ? 'text-signal-orange' : 'text-signal-green'} />
        <StatCard label="Total Runs" value={String(data.total_runs)} />
        <StatCard label="Failures" value={String(data.failures)} color="text-signal-red" />
        <StatCard label="Consecutive" value={String(data.consecutive_failures)} color={data.consecutive_failures > 2 ? 'text-signal-red' : 'text-text-primary'} />
      </div>

      {/* Classification breakdown */}
      {Object.keys(data.classifications).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(data.classifications).map(([cls, count]) => (
            <span key={cls} className="flex items-center gap-1">
              <ClassificationBadge classification={cls} />
              <span className="font-mono text-xs text-text-tertiary">x{count}</span>
            </span>
          ))}
        </div>
      )}

      {/* Recent runs */}
      {data.recent_runs.length > 0 && (
        <div>
          <h2 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-2">Recent Runs</h2>
          <Table>
            <TableHeader>
              <TableRow className="bg-surface-card hover:bg-surface-card">
                <TableHead>Job</TableHead>
                <TableHead>Classification</TableHead>
                <TableHead className="text-right">Date</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.recent_runs.map((run, i) => (
                <TableRow key={`${run.job_id}-${i}`} className={i % 2 === 0 ? 'bg-surface-card' : 'bg-surface-elevated/40'}>
                  <TableCell>
                    <Link to={`/results/${run.job_id}`} className="text-text-link hover:underline font-display text-xs">
                      {run.job_name} #{run.build_number}
                    </Link>
                  </TableCell>
                  <TableCell>
                    {run.classification && <ClassificationBadge classification={run.classification} />}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs text-text-tertiary">
                    {new Date(run.analyzed_at).toLocaleDateString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Comments */}
      {data.comments.length > 0 && (
        <div>
          <h2 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-2">
            Comments ({data.comments.length})
          </h2>
          <div className="space-y-2">
            {data.comments.map((c, i) => (
              <div key={`${c.created_at}-${i}`} className="rounded-md bg-surface-elevated/50 px-3 py-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-signal-blue">{c.username || 'anon'}</span>
                  <span className="text-[10px] text-text-tertiary">{new Date(c.created_at).toLocaleString()}</span>
                </div>
                <p className="mt-1 whitespace-pre-wrap text-text-secondary">{c.comment}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Times */}
      <div className="text-xs text-text-tertiary space-y-1">
        {data.first_seen && <p>First seen: {new Date(data.first_seen).toLocaleString()}</p>}
        {data.last_seen && <p>Last seen: {new Date(data.last_seen).toLocaleString()}</p>}
      </div>
    </div>
  )
}

function StatCard({ label, value, color = 'text-text-primary' }: { label: string; value: string; color?: string }) {
  return (
    <Card>
      <CardContent className="p-3 text-center">
        <p className="text-[10px] font-display uppercase tracking-widest text-text-tertiary">{label}</p>
        <p className={`text-xl font-display font-bold ${color}`}>{value}</p>
      </CardContent>
    </Card>
  )
}
