import { useCallback, useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'
import { parseApiTimestamp, isAnalysisTimeout, formatDuration } from '@/lib/utils'
import { groupFailures } from '@/lib/grouping'
import { useExpandCollapseAll } from '@/lib/useExpandCollapseAll'
import type { ResultResponse, CommentsAndReviews, AiConfig } from '@/types'
import { ReportProvider, useReportState, useReportDispatch, useRefreshEnrichments } from './report/ReportContext'
import { FailureCard } from './report/FailureCard'
import { ChildJobSection } from './report/ChildJobSection'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusChip } from '@/components/shared/StatusChip'
import { ExpandCollapseButtons } from '@/components/shared/ExpandCollapseButtons'
import { ExternalLink, CheckCircle2, Clock, Calendar, Cpu, Timer } from 'lucide-react'
import { reviewKey } from './report/ReportContext'
import type { ChildJobAnalysis } from '@/types'

/** Recursively collect all review keys from failures + nested children. */
function collectAllTestKeys(
  failures: { test_name: string }[],
  children: ChildJobAnalysis[],
): string[] {
  const keys: string[] = (failures ?? []).map((f) => reviewKey(f.test_name))
  for (const child of children ?? []) {
    keys.push(...(child.failures ?? []).map((f) => reviewKey(f.test_name, child.job_name, child.build_number)))
    keys.push(...collectAllTestKeys([], child.failed_children ?? []))
  }
  return keys
}

/** Recursively count all failures including nested children. */
function countAllFailures(failures: { test_name: string }[], children: ChildJobAnalysis[]): number {
  let count = (failures ?? []).length
  for (const child of children ?? []) {
    count += (child.failures ?? []).length
    count += countAllFailures([], child.failed_children ?? [])
  }
  return count
}

export function ReportPage() {
  const { jobId } = useParams<{ jobId: string }>()
  return (
    <ReportProvider key={jobId ?? 'unknown'}>
      <ReportContent />
    </ReportProvider>
  )
}

function ReportContent() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const state = useReportState()
  const dispatch = useReportDispatch()
  const refreshEnrichments = useRefreshEnrichments()

  // Capture hash fragment on mount and listen for changes (child-job deep linking)
  const [activeHash, setActiveHash] = useState(() => window.location.hash.replace(/^#/, ''))

  useEffect(() => {
    const onHashChange = () => setActiveHash(window.location.hash.replace(/^#/, ''))
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  useEffect(() => {
    if (!jobId) return

    let cancelled = false

    async function load() {
      dispatch({ type: 'SET_LOADING', payload: true })
      try {
        // Result is required
        const resultRes = await api.get<ResultResponse>(`/results/${jobId}`)
        if (cancelled) return

        // Check status first to avoid flash of wrong state
        if (resultRes.status === 'pending' || resultRes.status === 'running' || resultRes.status === 'waiting') {
          navigate(`/status/${jobId}`, { replace: true })
          return
        }

        if (!resultRes.result) {
          dispatch({ type: 'SET_ERROR', payload: 'No result data found.' })
          return
        }

        if (resultRes.status === 'failed') {
          const errorMsg = resultRes.result.error ?? 'Analysis failed'
          dispatch({ type: 'SET_ERROR', payload: String(errorMsg) })
          return
        }

        dispatch({ type: 'SET_RESULT', payload: { result: resultRes.result, createdAt: resultRes.created_at, completedAt: resultRes.completed_at ?? '', analysisStartedAt: resultRes.analysis_started_at ?? '' } })

        // Comments, AI configs, classifications, and capabilities are best-effort
        const [commentsResult, aiConfigsResult, classificationsResult, capabilitiesResult] = await Promise.allSettled([
          api.get<CommentsAndReviews>(`/results/${jobId}/comments`),
          api.get<AiConfig[]>('/ai-configs'),
          api.get<{ classifications: Array<{ test_name: string; classification: string; job_name: string; parent_job_name: string; reason: string; references_info: string; created_by: string; job_id: string; child_build_number: number; created_at: string }> }>(
            `/history/classifications?job_id=${jobId}`,
          ),
          api.get<{ github_issues: boolean; jira_bugs: boolean }>('/capabilities'),
        ])
        if (cancelled) return

        if (capabilitiesResult.status === 'fulfilled') {
          dispatch({ type: 'SET_GITHUB_AVAILABLE', payload: capabilitiesResult.value.github_issues })
          dispatch({ type: 'SET_JIRA_AVAILABLE', payload: capabilitiesResult.value.jira_bugs })
        }
        if (commentsResult.status === 'fulfilled') {
          dispatch({ type: 'SET_COMMENTS_AND_REVIEWS', payload: commentsResult.value })
          // Fetch enrichments once at report level
          if (jobId) refreshEnrichments(jobId)
        }
        if (aiConfigsResult.status === 'fulfilled') {
          dispatch({ type: 'SET_AI_CONFIGS', payload: aiConfigsResult.value })
        }
        if (classificationsResult.status === 'fulfilled') {
          const classMap: Record<string, string> = {}
          for (const c of classificationsResult.value.classifications ?? []) {
            // Use composite key to handle same test_name across different child jobs
            const key = reviewKey(c.test_name, c.job_name, c.child_build_number)
            classMap[key] = c.classification
          }
          dispatch({ type: 'SET_CLASSIFICATIONS', payload: classMap })
        }
      } catch (err) {
        if (!cancelled) {
          dispatch({ type: 'SET_ERROR', payload: err instanceof Error ? err.message : 'Failed to load report' })
        }
      }
    }

    load()
    return () => { cancelled = true }
  }, [jobId, navigate, dispatch])

  // Poll for new comments every 30 seconds (serialized with sequence counter)
  useEffect(() => {
    if (!jobId) return

    let cancelled = false
    let seq = 0
    const interval = setInterval(async () => {
      const thisSeq = ++seq
      try {
        const res = await api.get<CommentsAndReviews>(`/results/${jobId}/comments`)
        if (!cancelled && thisSeq === seq) {
          dispatch({ type: 'SET_COMMENTS_AND_REVIEWS', payload: res })
          // Refresh enrichments so link badges appear for new comments
          refreshEnrichments(jobId)
        }
      } catch {
        /* polling failure is non-critical */
      }
    }, 30000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [jobId, dispatch, refreshEnrichments])

  // Preserve scroll position across F5 refreshes
  const scrollKey = `jji-scroll-${jobId}`

  // Restore scroll after data loads (not on mount — skeleton is too short)
  useEffect(() => {
    if (state.loading || state.error) return
    const saved = sessionStorage.getItem(scrollKey)
    let raf1 = 0
    let raf2 = 0
    if (saved) {
      // Double rAF: first lets React commit DOM, second lets browser layout
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => {
          window.scrollTo(0, parseInt(saved, 10))
        })
      })
    }
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
    }
  }, [state.loading, state.error, scrollKey])

  // Save scroll position on scroll (debounced)
  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>
    function handleScroll() {
      clearTimeout(timeout)
      timeout = setTimeout(() => {
        sessionStorage.setItem(scrollKey, String(window.scrollY))
      }, 150)
    }
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      clearTimeout(timeout)
      window.removeEventListener('scroll', handleScroll)
    }
  }, [scrollKey])

  // Derived values (safe to compute even when result is null)
  const result = state.result
  const groups = result ? groupFailures(result.failures ?? []) : []
  const totalFailures = result ? countAllFailures(result.failures ?? [], result.child_job_analyses ?? []) : 0
  const allTestKeys = result ? collectAllTestKeys(result.failures ?? [], result.child_job_analyses ?? []) : []
  const reviewedCount = allTestKeys.filter((k) => state.reviews[k]?.reviewed).length

  // Expand/collapse all for top-level failure cards
  const getFailureKeys = useCallback(
    () => (result ? groups.map((g) => `jji-expand-${result.job_id}-${g.id}`) : []),
    [groups, result],
  )
  const { remountKey: failureRemountKey, expandAll: expandAllFailures, collapseAll: collapseAllFailures } =
    useExpandCollapseAll(getFailureKeys)

  // Expand/collapse all for child job sections (and their nested failure cards)
  const getChildKeys = useCallback(() => {
    if (!result) return []
    const keys: string[] = []
    const resultJobId = result.job_id
    function walk(children: ChildJobAnalysis[]) {
      for (const child of children ?? []) {
        keys.push(`jji-expand-${resultJobId}-child-${child.job_name}-${child.build_number}`)
        const childGroups = groupFailures(child.failures ?? [], `child-${child.job_name}-${child.build_number}`)
        for (const g of childGroups) {
          keys.push(`jji-expand-${resultJobId}-${g.id}`)
        }
        walk(child.failed_children ?? [])
      }
    }
    walk(result.child_job_analyses ?? [])
    return keys
  }, [result])
  const { remountKey: childRemountKey, expandAll: expandAllChildren, collapseAll: collapseAllChildren } =
    useExpandCollapseAll(getChildKeys)

  /* ---- Loading skeleton ---- */
  if (state.loading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-96" />
        <Skeleton className="h-24 w-full" />
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-32 w-full" />
        ))}
      </div>
    )
  }

  /* ---- Error ---- */
  if (state.error) {
    return (
      <div className="flex flex-col items-center justify-center py-20 animate-fade-in">
        <p className="text-signal-red text-sm">{state.error}</p>
      </div>
    )
  }

  // After early returns, result is guaranteed to be non-null
  if (!result) return null

  return (
    <div className="space-y-6 animate-fade-in">
      {/* ---- Sticky header ---- */}
      <div className="sticky top-14 z-40 -mx-4 bg-surface-page/95 backdrop-blur-sm px-4 py-3 border-b border-border-muted sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-display text-lg font-bold text-text-primary truncate">
            {result.job_name || result.job_id}
          </h1>
          {result.build_number > 0 && (
            result.jenkins_url ? (
              <a href={String(result.jenkins_url)} target="_blank" rel="noopener noreferrer" className="font-mono text-sm text-text-link hover:underline">
                #{result.build_number}
              </a>
            ) : (
              <span className="font-mono text-sm text-text-tertiary">#{result.build_number}</span>
            )
          )}
          <StatusChip status={isAnalysisTimeout(result.status, result.error, result.summary) ? 'timeout' : result.status} />
          <Badge variant="destructive" className="font-mono">
            {totalFailures} {totalFailures === 1 ? 'failure' : 'failures'}
          </Badge>
          {/* Review progress badge */}
          {totalFailures > 0 && (
            <span
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold font-display ${
                reviewedCount === totalFailures
                  ? 'bg-signal-green/15 text-signal-green'
                  : reviewedCount > 0
                    ? 'bg-signal-orange/15 text-signal-orange'
                    : 'bg-signal-red/12 text-signal-red'
              }`}
            >
              {reviewedCount === totalFailures ? (
                <><CheckCircle2 className="h-3 w-3" /> Reviewed</>
              ) : reviewedCount > 0 ? (
                `${reviewedCount}/${totalFailures} Reviewed`
              ) : (
                'Needs Review'
              )}
            </span>
          )}
          {result.ai_provider && (
            <Badge variant="outline" className="text-[10px]">
              {result.ai_provider}
              {result.ai_model ? ` / ${result.ai_model}` : ''}
            </Badge>
          )}
          {result.jenkins_url && (
            <a
              href={String(result.jenkins_url)}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto flex items-center gap-1 text-xs text-text-link hover:underline"
            >
              Jenkins <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </div>

      {/* ---- Metadata detail row ---- */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-text-tertiary animate-slide-up">
        {state.createdAt && (
          <span className="inline-flex items-center gap-1">
            <Calendar className="h-3 w-3" />
            {parseApiTimestamp(state.createdAt).toLocaleString()}
          </span>
        )}
        {state.completedAt && (state.analysisStartedAt || state.createdAt) && (
          <span className="inline-flex items-center gap-1">
            <Timer className="h-3 w-3" />
            {formatDuration(parseApiTimestamp(state.analysisStartedAt || state.createdAt), parseApiTimestamp(state.completedAt))}
          </span>
        )}
        {result.ai_provider && (
          <span className="inline-flex items-center gap-1">
            <Cpu className="h-3 w-3" />
            {result.ai_provider}{result.ai_model ? ` / ${result.ai_model}` : ''}
          </span>
        )}
        {state.completedAt && (
          <span className="inline-flex items-center gap-1">
            <Clock className="h-3 w-3" />
            Completed {parseApiTimestamp(state.completedAt).toLocaleString()}
          </span>
        )}
      </div>

      {/* ---- Key takeaway ---- */}
      {result.summary && (
        <div className="rounded-lg border-l-4 border-l-signal-orange bg-glow-orange p-4 animate-slide-up">
          <h2 className="text-xs font-display uppercase tracking-widest text-signal-orange mb-2">Key Takeaway</h2>
          <p className="text-sm text-text-secondary whitespace-pre-wrap">{result.summary}</p>
        </div>
      )}

      {/* ---- Top-level failures ---- */}
      {groups.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs font-display uppercase tracking-widest text-text-tertiary">
              Failures ({(result.failures ?? []).length})
            </h2>
            {groups.length >= 2 && (
              <ExpandCollapseButtons onExpandAll={expandAllFailures} onCollapseAll={collapseAllFailures} />
            )}
          </div>
          <div className="space-y-3" key={failureRemountKey}>
            {groups.map((g, i) => (
              <FailureCard key={g.id} group={g} jobId={result.job_id} index={i} />
            ))}
          </div>
        </section>
      )}

      {/* ---- Child jobs ---- */}
      {(result.child_job_analyses ?? []).length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs font-display uppercase tracking-widest text-text-tertiary">
              Child Jobs ({(result.child_job_analyses ?? []).length})
            </h2>
            <ExpandCollapseButtons onExpandAll={expandAllChildren} onCollapseAll={collapseAllChildren} />
          </div>
          <div className="space-y-6" key={childRemountKey}>
            {(result.child_job_analyses ?? []).map((child) => (
              <ChildJobSection key={`${child.job_name}-${child.build_number}`} child={child} jobId={result.job_id} activeHash={activeHash} />
            ))}
          </div>
        </section>
      )}

      {/* ---- Footer ---- */}
      <footer className="border-t border-border-muted pt-4 pb-8 text-xs text-text-tertiary space-y-1">
        <p>
          Job ID: <span className="font-mono">{result.job_id}</span>
        </p>
        {state.completedAt && <p>Completed: {parseApiTimestamp(state.completedAt).toLocaleString()}</p>}
        {result.ai_provider && (
          <p>
            AI: {result.ai_provider}
            {result.ai_model ? ` (${result.ai_model})` : ''}
          </p>
        )}
      </footer>
    </div>
  )
}
