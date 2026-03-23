import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'
import type { DashboardJob } from '@/types'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusChip } from '@/components/shared/StatusChip'
import { SearchInput } from '@/components/shared/SearchInput'
import { Pagination } from '@/components/shared/Pagination'
import { ConfirmDialog } from '@/components/shared/ConfirmDialog'
import { Trash2, MessageSquare, CheckCircle2, GitFork, AlertTriangle } from 'lucide-react'

const STATUS_BORDER: Record<string, string> = {
  completed: 'border-l-signal-green',
  running: 'border-l-signal-blue',
  pending: 'border-l-border-default',
  failed: 'border-l-signal-red',
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

export function DashboardPage() {
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<DashboardJob[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(20)
  const [deleteTarget, setDeleteTarget] = useState<DashboardJob | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchJobs = useCallback(async () => {
    setError(null)
    try {
      const data = await api.get<DashboardJob[]>('/api/dashboard?limit=500')
      setJobs(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchJobs()
    const interval = setInterval(fetchJobs, 10_000)
    return () => clearInterval(interval)
  }, [fetchJobs])
  useEffect(() => { setPage(1) }, [search, perPage])

  const filtered = useMemo(() => {
    if (!search) return jobs
    const q = search.toLowerCase()
    return jobs.filter((j) => (j.job_name ?? j.job_id).toLowerCase().includes(q))
  }, [jobs, search])

  const totalPages = Math.max(1, Math.ceil(filtered.length / perPage))
  const pageJobs = filtered.slice((page - 1) * perPage, page * perPage)

  async function handleDelete() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await api.delete(`/results/${deleteTarget.job_id}`)
      setJobs((prev) => prev.filter((j) => j.job_id !== deleteTarget.job_id))
      setDeleteTarget(null)
    } catch (err) {
      console.error('Failed to delete job:', err)
    } finally {
      setDeleting(false)
    }
  }

  function handleRowClick(job: DashboardJob) {
    const dest = job.status === 'pending' || job.status === 'running'
      ? `/status/${job.job_id}`
      : `/results/${job.job_id}`
    navigate(dest)
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="font-display text-xl font-bold text-text-primary">Dashboard</h1>
            <p className="mt-0.5 text-sm text-text-tertiary">
              {filtered.length} analysis {filtered.length === 1 ? 'run' : 'runs'}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <SearchInput value={search} onChange={setSearch} placeholder="Filter jobs..." className="w-64" />
            <Select value={String(perPage)} onValueChange={(v) => setPerPage(Number(v))}>
              <SelectTrigger className="w-20">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Error */}
        {error && <p className="text-center text-signal-red py-8">{error}</p>}

        {/* Table */}
        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-11 w-full" />
            ))}
          </div>
        ) : pageJobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-lg border border-border-muted bg-surface-card py-16 text-center animate-fade-in">
            <p className="text-text-secondary">
              {search ? 'No jobs match your search.' : 'No analysis runs yet.'}
            </p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="bg-surface-card hover:bg-surface-card">
                <TableHead className="w-[40%]">Job</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-center">Failures</TableHead>
                <TableHead className="text-center">Reviewed</TableHead>
                <TableHead className="text-center">Comments</TableHead>
                <TableHead className="text-center">Children</TableHead>
                <TableHead className="text-right">Created</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {pageJobs.map((job, i) => {
                const borderColor = STATUS_BORDER[job.status] ?? 'border-l-border-default'
                const failureCount = job.failure_count ?? 0

                return (
                  <TableRow
                    key={job.job_id}
                    className={`group cursor-pointer animate-slide-up ${i % 2 === 0 ? 'bg-surface-card' : 'bg-surface-elevated/40'}`}
                    style={{ animationDelay: `${i * 30}ms`, animationFillMode: 'backwards' }}
                    onClick={() => handleRowClick(job)}
                  >
                    {/* Job name + build (with left accent border) */}
                    <TableCell className={`border-l-4 ${borderColor}`}>
                      <span className="font-display text-sm font-medium text-text-primary">
                        {job.job_name || job.job_id}
                      </span>
                      {job.build_number !== undefined && (
                        <span className="ml-2 font-mono text-xs text-text-tertiary">#{job.build_number}</span>
                      )}
                    </TableCell>

                    {/* Status */}
                    <TableCell>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span><StatusChip status={job.status} /></span>
                        </TooltipTrigger>
                        <TooltipContent>Analysis status: {job.status}</TooltipContent>
                      </Tooltip>
                    </TableCell>

                    {/* Failures */}
                    <TableCell className="text-center">
                      {failureCount > 0 ? (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex items-center gap-1 font-mono text-xs text-signal-red">
                              <AlertTriangle className="h-3 w-3" />
                              {failureCount}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>{failureCount} test {failureCount === 1 ? 'failure' : 'failures'}</TooltipContent>
                        </Tooltip>
                      ) : (
                        <span className="text-xs text-text-tertiary">—</span>
                      )}
                    </TableCell>

                    {/* Reviewed */}
                    <TableCell className="text-center">
                      {failureCount > 0 ? (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className={`inline-flex items-center gap-1 font-mono text-xs ${
                              job.reviewed_count === failureCount
                                ? 'text-signal-green'
                                : job.reviewed_count > 0
                                  ? 'text-signal-orange'
                                  : 'text-signal-red'
                            }`}>
                              <CheckCircle2 className="h-3 w-3" />
                              {job.reviewed_count}/{failureCount}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>
                            {job.reviewed_count} of {failureCount} failures reviewed
                          </TooltipContent>
                        </Tooltip>
                      ) : (
                        <span className="text-xs text-text-tertiary">—</span>
                      )}
                    </TableCell>

                    {/* Comments */}
                    <TableCell className="text-center">
                      {job.comment_count > 0 ? (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex items-center gap-1 font-mono text-xs text-text-secondary">
                              <MessageSquare className="h-3 w-3" />
                              {job.comment_count}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>{job.comment_count} {job.comment_count === 1 ? 'comment' : 'comments'}</TooltipContent>
                        </Tooltip>
                      ) : (
                        <span className="text-xs text-text-tertiary">—</span>
                      )}
                    </TableCell>

                    {/* Children */}
                    <TableCell className="text-center">
                      {job.child_job_count !== undefined && job.child_job_count > 0 ? (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex items-center gap-1 font-mono text-xs text-text-secondary">
                              <GitFork className="h-3 w-3" />
                              {job.child_job_count}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>{job.child_job_count} child {job.child_job_count === 1 ? 'job' : 'jobs'}</TooltipContent>
                        </Tooltip>
                      ) : (
                        <span className="text-xs text-text-tertiary">—</span>
                      )}
                    </TableCell>

                    {/* Created */}
                    <TableCell className="text-right">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="font-mono text-xs text-text-tertiary">{relativeTime(job.created_at)}</span>
                        </TooltipTrigger>
                        <TooltipContent>{new Date(job.created_at).toLocaleString()}</TooltipContent>
                      </Tooltip>
                    </TableCell>

                    {/* Delete */}
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 opacity-0 transition-opacity group-hover:opacity-100"
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteTarget(job)
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-text-tertiary hover:text-signal-red" />
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}

        <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />

        <ConfirmDialog
          open={deleteTarget !== null}
          onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
          title="Delete analysis"
          description={`Permanently delete "${deleteTarget?.job_name || deleteTarget?.job_id}"? This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          onConfirm={handleDelete}
          loading={deleting}
        />
      </div>
    </TooltipProvider>
  )
}
