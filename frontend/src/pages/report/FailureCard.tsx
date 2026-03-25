import { useState, useRef, useEffect } from 'react'
import type { GroupedFailure } from '@/types'
import { isCommentInScope } from '@/lib/grouping'
import { api } from '@/lib/api'
import { getUsername } from '@/lib/cookies'
import { useSessionState } from '@/lib/useSessionState'
import { useReportState, useReportDispatch, reviewKey } from './ReportContext'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ClassificationBadge } from '@/components/shared/ClassificationBadge'
import { ReviewToggle } from './ReviewToggle'
import { CommentsSection } from './CommentsSection'
import { ClassificationSelect } from './ClassificationSelect'
import { BugCreationDialog } from './BugCreationDialog'
import { ChevronDown, ChevronRight, Bug, MessageSquare, CheckCircle2, Copy, Check, Clock } from 'lucide-react'

interface FailureCardProps {
  group: GroupedFailure
  jobId: string
  childJobName?: string
  childBuildNumber?: number
  index: number
}

export function FailureCard({ group, jobId, childJobName, childBuildNumber, index }: FailureCardProps) {
  const { githubAvailable, jiraAvailable, comments, reviews, aiConfigs, result, classifications } = useReportState()
  const dispatch = useReportDispatch()
  const expandKey = `jji-expand-${jobId}-${group.id}`
  const [expanded, setExpanded] = useSessionState(expandKey, false)
  const [bugTarget, setBugTarget] = useState<'github' | 'jira' | null>(null)
  const [reviewingAll, setReviewingAll] = useState(false)
  const [reviewAllError, setReviewAllError] = useState<string | null>(null)
  const [selectedProvider, setSelectedProvider] = useState(result?.ai_provider ?? '')
  const [selectedModel, setSelectedModel] = useState(result?.ai_model ?? '')
  const [includeLinks, setIncludeLinks] = useState(false)
  const [copiedSection, setCopiedSection] = useState<string | null>(null)
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout>>(null)

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current)
    }
  }, [])

  function copyToClipboard(text: string, section: string) {
    void navigator.clipboard.writeText(text).then(() => {
      setCopiedSection(section)
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current)
      copyTimeoutRef.current = setTimeout(() => setCopiedSection(null), 2000)
    }).catch(() => {})
  }

  function getModelsForProvider(provider: string) {
    return [...new Set(aiConfigs.filter((c) => c.ai_provider === provider).map((c) => c.ai_model))]
  }

  const providers = [...new Set(aiConfigs.map((c) => c.ai_provider))]
  const models = getModelsForProvider(selectedProvider)
  const showAiSelector = providers.length > 0 || models.length > 0

  function handleProviderChange(provider: string) {
    setSelectedProvider(provider)
    const providerModels = getModelsForProvider(provider)
    if (providerModels.length === 0) {
      setSelectedModel('')
    } else if (!providerModels.includes(selectedModel)) {
      setSelectedModel(providerModels[0])
    }
  }

  const rep = group.tests[0]
  const analysis = rep.analysis
  const repKey = reviewKey(rep.test_name, childJobName, childBuildNumber)
  const classification = classifications[repKey] ?? analysis.classification
  const borderColor = classification === 'PRODUCT BUG' ? 'border-l-signal-orange' : 'border-l-signal-blue'

  // Comment count for ALL tests in the group
  const groupTestNames = group.tests.map((t) => t.test_name)
  const commentCount = comments.filter((c) => isCommentInScope(c, groupTestNames, childJobName, childBuildNumber)).length

  // Review-all: check how many tests in group are reviewed
  const reviewedCount = group.tests.filter((t) => {
    const key = reviewKey(t.test_name, childJobName, childBuildNumber)
    return reviews[key]?.reviewed
  }).length
  const allReviewed = reviewedCount === group.tests.length

  async function handleReviewAll() {
    setReviewingAll(true)
    setReviewAllError(null)
    const newState = !allReviewed
    try {
      const results = await Promise.allSettled(
        group.tests.map((t) =>
          api.put<{ status: string; reviewed_by: string }>(`/results/${jobId}/reviewed`, {
            test_name: t.test_name,
            reviewed: newState,
            child_job_name: childJobName ?? '',
            child_build_number: childBuildNumber ?? 0,
          }).then((res) => ({ test: t, reviewed_by: res.reviewed_by })),
        ),
      )
      let failedCount = 0
      for (const result of results) {
        if (result.status === 'fulfilled') {
          const { test: t, reviewed_by } = result.value
          const key = reviewKey(t.test_name, childJobName, childBuildNumber)
          dispatch({
            type: 'SET_REVIEW',
            payload: { key, state: { reviewed: newState, updated_at: new Date().toISOString(), username: reviewed_by || getUsername() } },
          })
        } else {
          failedCount++
        }
      }
      if (failedCount > 0) {
        setReviewAllError(`Failed to update ${failedCount} of ${results.length} tests`)
      }
    } finally {
      setReviewingAll(false)
    }
  }

  return (
    <>
      <Card
        className={`border-l-4 ${borderColor} animate-slide-up`}
        style={{ animationDelay: `${index * 50}ms`, animationFillMode: 'backwards' }}
      >
        {/* Header */}
        <div className="flex w-full items-center gap-3 p-4">
          <button
            className="flex min-w-0 flex-1 items-center gap-3 text-left"
            onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
          >
            {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-text-tertiary" /> : <ChevronRight className="h-4 w-4 shrink-0 text-text-tertiary" />}
            <div className="min-w-0 flex-1">
              <p className="truncate font-display text-sm font-medium text-text-primary">{rep.test_name}</p>
              {group.count > 1 && <span className="text-xs text-text-tertiary">+{group.count - 1} more with same error</span>}
            </div>
          </button>
          <div className="flex items-center gap-2 shrink-0">
            <ClassificationBadge classification={classification} />
            {(() => {
              const secondaryBadges = new Set<string>()
              for (const t of group.tests) {
                const key = reviewKey(t.test_name, childJobName, childBuildNumber)
                const cls = classifications[key]
                if (cls && cls !== classification) secondaryBadges.add(cls)
              }
              return [...secondaryBadges].map((cls) => (
                <ClassificationBadge key={cls} classification={cls} />
              ))
            })()}
            <ReviewToggle jobId={jobId} testName={rep.test_name} childJobName={childJobName} childBuildNumber={childBuildNumber} />
            {commentCount > 0 && (
              <span className="flex items-center gap-1 rounded-md bg-surface-elevated px-2 py-1 text-[10px] font-mono text-text-tertiary">
                <MessageSquare className="h-3 w-3" />
                {commentCount}
              </span>
            )}
          </div>
        </div>

        {/* Expanded body */}
        {expanded && (
          <CardContent className="space-y-4 border-t border-border-muted pt-4">
            {/* Review-all toggle for groups */}
            {group.count > 1 && (
              <div className="flex items-center gap-2">
                <button
                  onClick={handleReviewAll}
                  disabled={reviewingAll}
                  className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-bold transition-colors ${
                    allReviewed
                      ? 'bg-signal-green/15 text-signal-green'
                      : 'bg-surface-elevated text-text-tertiary hover:text-text-secondary'
                  }`}
                >
                  <CheckCircle2 className="h-4 w-4" />
                  {allReviewed ? 'All Reviewed' : `Review All (${reviewedCount}/${group.count})`}
                </button>
                {reviewAllError && <span className="text-signal-red text-xs">{reviewAllError}</span>}
              </div>
            )}

            {/* Affected tests list */}
            {group.count > 1 && (
              <div>
                <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-2">Affected Tests ({group.count})</h4>
                <div className="space-y-1">
                  {group.tests.map((t) => (
                    <div key={t.test_name} className="flex items-center justify-between gap-2">
                      <p className="font-mono text-xs text-text-secondary truncate">{t.test_name}</p>
                      <ReviewToggle jobId={jobId} testName={t.test_name} childJobName={childJobName} childBuildNumber={childBuildNumber} />
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Error */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary">Error</h4>
                <button
                  type="button"
                  className="text-text-tertiary hover:text-text-primary transition-colors"
                  onClick={() => copyToClipboard(rep.error, 'error')}
                  title="Copy to clipboard"
                >
                  {copiedSection === 'error' ? <Check className="h-3 w-3 text-signal-green" /> : <Copy className="h-3 w-3" />}
                </button>
              </div>
              <pre className="overflow-x-auto rounded-md bg-signal-red/5 border border-signal-red/20 p-3 text-xs text-signal-red font-mono whitespace-pre-wrap max-h-48 overflow-y-auto">
                {rep.error}
              </pre>
            </div>

            {/* Analysis */}
            {analysis.details && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary">Analysis</h4>
                    {analysis.details.toLowerCase().includes('timed out') && (
                      <Badge variant="warning" className="text-[10px] gap-1">
                        <Clock className="h-3 w-3" />
                        Timed Out
                      </Badge>
                    )}
                  </div>
                  <button
                    type="button"
                    className="text-text-tertiary hover:text-text-primary transition-colors"
                    onClick={() => copyToClipboard(analysis.details, 'analysis')}
                    title="Copy to clipboard"
                  >
                    {copiedSection === 'analysis' ? <Check className="h-3 w-3 text-signal-green" /> : <Copy className="h-3 w-3" />}
                  </button>
                </div>
                <div className="rounded-md bg-glow-blue p-3 text-sm text-text-secondary whitespace-pre-wrap">{analysis.details}</div>
              </div>
            )}

            {/* Artifacts evidence */}
            {analysis.artifacts_evidence && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary">Artifacts Evidence</h4>
                  <button
                    type="button"
                    className="text-text-tertiary hover:text-text-primary transition-colors"
                    onClick={() => copyToClipboard(analysis.artifacts_evidence, 'artifacts_evidence')}
                    title="Copy to clipboard"
                  >
                    {copiedSection === 'artifacts_evidence' ? <Check className="h-3 w-3 text-signal-green" /> : <Copy className="h-3 w-3" />}
                  </button>
                </div>
                <pre className="overflow-x-auto rounded-md bg-surface-elevated p-3 text-xs text-text-secondary font-mono whitespace-pre-wrap max-h-64 overflow-y-auto">
                  {analysis.artifacts_evidence}
                </pre>
              </div>
            )}

            {/* Code fix */}
            {classification !== 'PRODUCT BUG' && analysis.code_fix && typeof analysis.code_fix === 'object' && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary">Suggested Fix</h4>
                  <button
                    type="button"
                    className="text-text-tertiary hover:text-text-primary transition-colors"
                    onClick={() => {
                      const parts: string[] = []
                      if (analysis.code_fix?.file) parts.push(`${analysis.code_fix.file}${analysis.code_fix.line ? `:${analysis.code_fix.line}` : ''}`)
                      if (analysis.code_fix?.change) parts.push(analysis.code_fix.change)
                      copyToClipboard(parts.join('\n'), 'suggested_fix')
                    }}
                    title="Copy to clipboard"
                  >
                    {copiedSection === 'suggested_fix' ? <Check className="h-3 w-3 text-signal-green" /> : <Copy className="h-3 w-3" />}
                  </button>
                </div>
                <div className="rounded-md bg-glow-green border border-signal-green/20 p-3 text-sm">
                  {analysis.code_fix.file && (
                    <p className="font-mono text-xs text-signal-green">
                      {analysis.code_fix.file}
                      {analysis.code_fix.line && `:${analysis.code_fix.line}`}
                    </p>
                  )}
                  {analysis.code_fix.change && <p className="mt-1 text-text-secondary whitespace-pre-wrap">{analysis.code_fix.change}</p>}
                </div>
              </div>
            )}

            {/* Product bug report */}
            {classification === 'PRODUCT BUG' && analysis.product_bug_report && typeof analysis.product_bug_report === 'object' && (
              <div>
                <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-2">Bug Report</h4>
                <div className="rounded-md bg-glow-orange border border-signal-orange/20 p-3 text-sm space-y-2">
                  {analysis.product_bug_report.title && <p className="font-medium text-text-primary">{analysis.product_bug_report.title}</p>}
                  {analysis.product_bug_report.severity && <Badge variant="warning" className="text-[10px]">{analysis.product_bug_report.severity}</Badge>}
                  {analysis.product_bug_report.description && <p className="text-text-secondary whitespace-pre-wrap">{analysis.product_bug_report.description}</p>}
                  {analysis.product_bug_report.jira_matches?.length > 0 && (
                    <div className="mt-2">
                      <p className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-1">Matching Jira Issues</p>
                      <ul className="space-y-1">
                        {analysis.product_bug_report.jira_matches.map((m) => (
                          <li key={m.key} className="flex items-center gap-2 text-xs">
                            <a href={m.url} target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline">
                              {m.key}: {m.summary}
                            </a>
                            {m.status && <Badge variant="outline" className="text-[10px]">{m.status}</Badge>}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Actions: classification + AI selector + bug creation */}
            <div className="flex flex-wrap items-center gap-3 pt-2 border-t border-border-muted">
              <ClassificationSelect
                jobId={jobId}
                testName={rep.test_name}
                currentClassification={classification}
                childJobName={childJobName}
                childBuildNumber={childBuildNumber}
              />
              {showAiSelector && (
                <>
                  <span className="text-xs text-text-tertiary whitespace-nowrap">AI for issue generation:</span>
                  <div className="flex items-center gap-2">
                    <input
                      list={`provider-options-${group.id}`}
                      value={selectedProvider}
                      onChange={(e) => handleProviderChange(e.target.value)}
                      placeholder="provider"
                      className="h-7 rounded-md border border-border-default bg-surface-card px-2 text-xs text-text-primary w-24"
                    />
                    <datalist id={`provider-options-${group.id}`}>
                      {providers.map((p) => (
                        <option key={p} value={p} />
                      ))}
                    </datalist>

                    <input
                      list={`model-options-${group.id}`}
                      value={selectedModel}
                      onChange={(e) => setSelectedModel(e.target.value)}
                      placeholder="model"
                      className="h-7 rounded-md border border-border-default bg-surface-card px-2 text-xs text-text-primary w-44"
                    />
                    <datalist id={`model-options-${group.id}`}>
                      {models.map((m) => (
                        <option key={m} value={m} />
                      ))}
                    </datalist>
                  </div>
                </>
              )}
              <label className="flex items-center gap-1.5 text-xs text-text-secondary cursor-pointer">
                <input
                  type="checkbox"
                  checked={includeLinks}
                  onChange={(e) => setIncludeLinks(e.target.checked)}
                  className="rounded border-border-default"
                />
                Include links
              </label>
              {classification !== 'PRODUCT BUG' && githubAvailable && (
                <Button variant="outline" size="sm" onClick={() => setBugTarget('github')}>
                  <Bug className="h-3.5 w-3.5 mr-1" /> GitHub Issue
                </Button>
              )}
              {classification === 'PRODUCT BUG' && jiraAvailable && (
                <Button variant="outline" size="sm" onClick={() => setBugTarget('jira')}>
                  <Bug className="h-3.5 w-3.5 mr-1" /> Jira Bug
                </Button>
              )}
            </div>

            {/* Comments */}
            <CommentsSection jobId={jobId} testNames={groupTestNames} childJobName={childJobName} childBuildNumber={childBuildNumber} />
          </CardContent>
        )}
      </Card>

      {bugTarget && (
        <BugCreationDialog
          open={bugTarget !== null}
          onOpenChange={(o) => { if (!o) setBugTarget(null) }}
          jobId={jobId}
          testName={rep.test_name}
          includeLinks={includeLinks}
          target={bugTarget}
          childJobName={childJobName}
          childBuildNumber={childBuildNumber}
          aiProvider={selectedProvider}
          aiModel={selectedModel}
        />
      )}
    </>
  )
}
