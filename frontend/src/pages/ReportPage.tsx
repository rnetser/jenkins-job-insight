import { useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'
import { groupFailures } from '@/lib/grouping'
import type { ResultResponse, CommentsAndReviews, AiConfig, CommentEnrichment } from '@/types'
import { ReportProvider, useReportState, useReportDispatch } from './report/ReportContext'
import { FailureCard } from './report/FailureCard'
import { ChildJobSection } from './report/ChildJobSection'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusChip } from '@/components/shared/StatusChip'
import { ExternalLink, CheckCircle2 } from 'lucide-react'
import { reviewKey } from './report/ReportContext'
import type { ChildJobAnalysis } from '@/types'

/** Recursively collect all review keys from failures + nested children. */
function collectAllTestKeys(
  failures: { test_name: string }[],
  children: ChildJobAnalysis[],
  keys: string[] = [],
): string[] {
  for (const f of failures) keys.push(reviewKey(f.test_name))
  for (const child of children) {
    for (const f of child.failures) keys.push(reviewKey(f.test_name, child.job_name, child.build_number))
    collectAllTestKeys([], child.failed_children, keys)
  }
  return keys
}

/** Recursively count all failures including nested children. */
function countAllFailures(failures: { test_name: string }[], children: ChildJobAnalysis[]): number {
  let count = failures.length
  for (const child of children) {
    count += child.failures.length
    count += countAllFailures([], child.failed_children)
  }
  return count
}

export function ReportPage() {
  return (
    <ReportProvider>
      <ReportContent />
    </ReportProvider>
  )
}

function ReportContent() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const state = useReportState()
  const dispatch = useReportDispatch()

  useEffect(() => {
    if (!jobId) return

    async function load() {
      dispatch({ type: 'SET_LOADING', payload: true })
      try {
        // Result is required
        const resultRes = await api.get<ResultResponse>(`/results/${jobId}`)

        if (!resultRes.result) {
          if (resultRes.status === 'pending' || resultRes.status === 'running') {
            navigate(`/status/${jobId}`, { replace: true })
            return
          }
          dispatch({ type: 'SET_ERROR', payload: 'No result data found.' })
          return
        }

        if (resultRes.result && resultRes.status === 'failed') {
          const errorMsg = (resultRes.result as any).error || 'Analysis failed'
          dispatch({ type: 'SET_ERROR', payload: String(errorMsg) })
          return
        }

        dispatch({ type: 'SET_RESULT', payload: { result: resultRes.result, completedAt: resultRes.completed_at ?? '' } })

        // Comments, AI configs, classifications, and capabilities are best-effort
        const [commentsResult, aiConfigsResult, classificationsResult, capabilitiesResult] = await Promise.allSettled([
          api.get<CommentsAndReviews>(`/results/${jobId}/comments`),
          api.get<AiConfig[]>('/ai-configs'),
          api.get<{ classifications: Array<{ test_name: string; classification: string }> }>(
            `/history/classifications?job_id=${jobId}`,
          ),
          api.get<{ github_issues: boolean; jira_bugs: boolean }>('/api/capabilities'),
        ])
        if (capabilitiesResult.status === 'fulfilled') {
          dispatch({ type: 'SET_GITHUB_AVAILABLE', payload: capabilitiesResult.value.github_issues })
          dispatch({ type: 'SET_JIRA_AVAILABLE', payload: capabilitiesResult.value.jira_bugs })
        }
        if (commentsResult.status === 'fulfilled') {
          dispatch({ type: 'SET_COMMENTS_AND_REVIEWS', payload: commentsResult.value })
          // Fetch enrichments once at report level
          api.post<{ enrichments: Record<string, CommentEnrichment[]> }>(`/results/${jobId}/enrich-comments`)
            .then((res) => dispatch({ type: 'SET_ENRICHMENTS', payload: res.enrichments ?? {} }))
            .catch(() => {}) // best-effort
        }
        if (aiConfigsResult.status === 'fulfilled') {
          dispatch({ type: 'SET_AI_CONFIGS', payload: aiConfigsResult.value })
        }
        if (classificationsResult.status === 'fulfilled') {
          const classMap: Record<string, string> = {}
          for (const c of (classificationsResult.value as any).classifications ?? []) {
            // Use composite key to handle same test_name across different child jobs
            const key = c.job_name && c.child_build_number
              ? `${c.job_name}#${c.child_build_number}::${c.test_name}`
              : c.test_name
            classMap[key] = c.classification
          }
          dispatch({ type: 'SET_CLASSIFICATIONS', payload: classMap })
        }
      } catch (err) {
        dispatch({ type: 'SET_ERROR', payload: err instanceof Error ? err.message : 'Failed to load report' })
      }
    }

    load()
  }, [jobId, navigate, dispatch])

  // Preserve scroll position across F5 refreshes
  const scrollKey = `jji-scroll-${jobId}`

  // Restore scroll after data loads (not on mount — skeleton is too short)
  useEffect(() => {
    if (state.loading || state.error) return
    const saved = sessionStorage.getItem(scrollKey)
    if (saved) {
      // Double rAF: first lets React commit DOM, second lets browser layout
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          window.scrollTo(0, parseInt(saved, 10))
        })
      })
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

  const result = state.result!
  const groups = groupFailures(result.failures)
  const totalFailures = countAllFailures(result.failures, result.child_job_analyses)
  const allTestKeys = collectAllTestKeys(result.failures, result.child_job_analyses)
  const reviewedCount = allTestKeys.filter((k) => state.reviews[k]?.reviewed).length

  return (
    <div className="space-y-6 animate-fade-in">
      {/* ---- Sticky header ---- */}
      <div className="sticky top-14 z-40 -mx-4 bg-surface-page/95 backdrop-blur-sm px-4 py-3 border-b border-border-muted sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-display text-lg font-bold text-text-primary truncate">
            {result.job_name || result.job_id}
          </h1>
          {result.build_number > 0 && (
            <span className="font-mono text-sm text-text-tertiary">#{result.build_number}</span>
          )}
          <StatusChip status={result.status} />
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
          <h2 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-3">
            Failures ({result.failures.length})
          </h2>
          <div className="space-y-3">
            {groups.map((g, i) => (
              <FailureCard key={g.id} group={g} jobId={result.job_id} index={i} />
            ))}
          </div>
        </section>
      )}

      {/* ---- Child jobs ---- */}
      {result.child_job_analyses.length > 0 && (
        <section>
          <h2 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-3">
            Child Jobs ({result.child_job_analyses.length})
          </h2>
          <div className="space-y-6">
            {result.child_job_analyses.map((child) => (
              <ChildJobSection key={`${child.job_name}-${child.build_number}`} child={child} jobId={result.job_id} />
            ))}
          </div>
        </section>
      )}

      {/* ---- Footer ---- */}
      <footer className="border-t border-border-muted pt-4 pb-8 text-xs text-text-tertiary space-y-1">
        <p>
          Job ID: <span className="font-mono">{result.job_id}</span>
        </p>
        {state.completedAt && <p>Completed: {new Date(state.completedAt).toLocaleString()}</p>}
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
