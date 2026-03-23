import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'
import type { FailureHistoryEntry } from '@/types'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { SearchInput } from '@/components/shared/SearchInput'
import { Pagination } from '@/components/shared/Pagination'
import { ClassificationBadge } from '@/components/shared/ClassificationBadge'

const CLASSIFICATIONS = [
  'ALL',
  'CODE ISSUE',
  'PRODUCT BUG',
  'FLAKY',
  'REGRESSION',
  'INFRASTRUCTURE',
  'KNOWN_BUG',
  'INTERMITTENT',
]

const LIMIT = 50

/* ================================================================== */
/*  HistoryPage                                                        */
/* ================================================================== */

export function HistoryPage() {
  return (
    <div className="space-y-6">
      <h1 className="font-display text-xl font-bold text-text-primary">
        Failure History
      </h1>

      <FailureHistoryTab />
    </div>
  )
}

/* ================================================================== */
/*  Failure History Tab                                                 */
/* ================================================================== */

function FailureHistoryTab() {
  const navigate = useNavigate()
  const [data, setData] = useState<FailureHistoryEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [classification, setClassification] = useState('ALL')
  const [page, setPage] = useState(1)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null)

  const fetchData = useCallback(
    async (s: string, cls: string, p: number) => {
      setLoading(true)
      setError(null)
      try {
        const params = new URLSearchParams({
          limit: String(LIMIT),
          offset: String((p - 1) * LIMIT),
        })
        if (s) params.set('search', s)
        if (cls && cls !== 'ALL') params.set('classification', cls)

        const res = await api.get<{ failures: FailureHistoryEntry[]; total: number }>(
          `/history/failures?${params}`,
        )
        setData(res.failures)
        setTotal(res.total)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load history')
      } finally {
        setLoading(false)
      }
    },
    [],
  )

  // Fetch on page change immediately
  useEffect(() => {
    fetchData(search, classification, page)
  }, [page, classification, fetchData]) // eslint-disable-line react-hooks/exhaustive-deps

  // Debounce search
  function handleSearch(v: string) {
    setSearch(v)
    setPage(1)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchData(v, classification, 1), 300)
  }

  function handleClassification(v: string) {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    setClassification(v)
    setPage(1)
  }

  const totalPages = Math.max(1, Math.ceil(total / LIMIT))

  return (
    <div className="space-y-4 pt-4">
      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <SearchInput
          value={search}
          onChange={handleSearch}
          placeholder="Search test names..."
          className="w-full sm:w-72"
        />
        <Select value={classification} onValueChange={handleClassification}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="Classification" />
          </SelectTrigger>
          <SelectContent>
            {CLASSIFICATIONS.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="ml-auto text-xs text-text-tertiary font-mono">
          {total} result{total !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Error */}
      {error && <p className="text-center text-signal-red py-8">{error}</p>}

      {/* Table */}
      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : data.length === 0 ? (
        <div className="flex items-center justify-center rounded-lg border border-border-muted bg-surface-card py-16 animate-fade-in">
          <p className="text-text-secondary">No failures found.</p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="bg-surface-card">
              <TableHead className="w-[40%]">Test Name</TableHead>
              <TableHead>Job</TableHead>
              <TableHead>Classification</TableHead>
              <TableHead className="text-right">Date</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((entry, i) => (
              <TableRow
                key={entry.id}
                className={`cursor-pointer animate-slide-up ${i % 2 === 0 ? 'bg-surface-card' : 'bg-surface-elevated/40'}`}
                style={{
                  animationDelay: `${i * 30}ms`,
                  animationFillMode: 'backwards',
                }}
                onClick={() => navigate(`/results/${entry.job_id}`)}
              >
                <TableCell className="font-mono text-xs text-text-primary max-w-[400px]">
                  <Link
                    to={`/history/test/${encodeURIComponent(entry.test_name)}`}
                    className="text-text-link hover:underline truncate block"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {entry.test_name}
                  </Link>
                </TableCell>
                <TableCell>
                  <span className="font-display text-xs text-text-secondary">
                    {entry.job_name}
                  </span>
                  <span className="ml-1 font-mono text-[10px] text-text-tertiary">
                    #{entry.build_number}
                  </span>
                </TableCell>
                <TableCell>
                  {entry.classification && (
                    <ClassificationBadge classification={entry.classification} />
                  )}
                </TableCell>
                <TableCell className="text-right font-mono text-xs text-text-tertiary whitespace-nowrap">
                  {new Date(entry.analyzed_at).toLocaleDateString()}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
    </div>
  )
}
