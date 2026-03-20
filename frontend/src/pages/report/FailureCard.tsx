import { useState } from 'react'
import type { GroupedFailure } from '@/types'
import { api } from '@/lib/api'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ChevronDown, ChevronRight, Bug, MessageSquare, CheckCircle2 } from 'lucide-react'

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
  const [selectedProvider, setSelectedProvider] = useState(result?.ai_provider ?? '')
  const [selectedModel, setSelectedModel] = useState(result?.ai_model ?? '')
  const [includeLinks, setIncludeLinks] = useState(false)

  const providers = [...new Set(aiConfigs.map((c) => c.ai_provider))]
  const models = [...new Set(aiConfigs.map((c) => c.ai_model))]
  const showAiSelector = providers.length > 0 || models.length > 0

  const rep = group.tests[0]
  const analysis = rep.analysis
  const classification = analysis.classification
  const borderColor = classification === 'PRODUCT BUG' ? 'border-l-signal-orange' : 'border-l-signal-blue'

  // Comment count for ALL tests in the group
  const groupTestNames = group.tests.map((t) => t.test_name)
  const commentCount = comments.filter((c) => {
    if (!groupTestNames.includes(c.test_name)) return false
    if (childJobName) return c.child_job_name === childJobName && c.child_build_number === childBuildNumber
    return !c.child_job_name
  }).length

  // Review-all: check how many tests in group are reviewed
  const reviewedCount = group.tests.filter((t) => {
    const key = reviewKey(t.test_name, childJobName, childBuildNumber)
    return reviews[key]?.reviewed
  }).length
  const allReviewed = reviewedCount === group.tests.length

  async function handleReviewAll() {
    setReviewingAll(true)
    const newState = !allReviewed
    try {
      await Promise.all(
        group.tests.map((t) =>
          api.put(`/results/${jobId}/reviewed`, {
            test_name: t.test_name,
            reviewed: newState,
            child_job_name: childJobName ?? '',
            child_build_number: childBuildNumber ?? 0,
          }).then(() => {
            const key = reviewKey(t.test_name, childJobName, childBuildNumber)
            dispatch({
              type: 'SET_REVIEW',
              payload: { key, state: { reviewed: newState, updated_at: new Date().toISOString() } },
            })
          }),
        ),
      )
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
        <button className="flex w-full items-center gap-3 p-4 text-left" onClick={() => setExpanded(!expanded)}>
          {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-text-tertiary" /> : <ChevronRight className="h-4 w-4 shrink-0 text-text-tertiary" />}
          <div className="min-w-0 flex-1">
            <p className="truncate font-display text-sm font-medium text-text-primary">{rep.test_name}</p>
            {group.count > 1 && <span className="text-xs text-text-tertiary">+{group.count - 1} more with same error</span>}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <ClassificationBadge classification={classification} />
            {(() => {
              const secondaryBadges = new Set<string>()
              for (const t of group.tests) {
                // Use scoped key for child jobs, plain test_name for root — no cross-scope fallback
                const key = childJobName && childBuildNumber
                  ? `${childJobName}#${childBuildNumber}::${t.test_name}`
                  : t.test_name
                const cls = classifications[key]
                if (cls) secondaryBadges.add(cls)
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
        </button>

        {/* Expanded body */}
        {expanded && (
          <CardContent className="space-y-4 border-t border-border-muted pt-4">
            {/* Review-all toggle for groups */}
            {group.count > 1 && (
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
              <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-2">Error</h4>
              <pre className="overflow-x-auto rounded-md bg-signal-red/5 border border-signal-red/20 p-3 text-xs text-signal-red font-mono whitespace-pre-wrap max-h-48 overflow-y-auto">
                {rep.error}
              </pre>
            </div>

            {/* Analysis */}
            {analysis.details && (
              <div>
                <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-2">Analysis</h4>
                <div className="rounded-md bg-glow-blue p-3 text-sm text-text-secondary whitespace-pre-wrap">{analysis.details}</div>
              </div>
            )}

            {/* Artifacts evidence */}
            {analysis.artifacts_evidence && (
              <div>
                <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-2">Artifacts Evidence</h4>
                <pre className="overflow-x-auto rounded-md bg-surface-elevated p-3 text-xs text-text-secondary font-mono whitespace-pre-wrap max-h-64 overflow-y-auto">
                  {analysis.artifacts_evidence}
                </pre>
              </div>
            )}

            {/* Code fix */}
            {analysis.code_fix && typeof analysis.code_fix === 'object' && (
              <div>
                <h4 className="text-xs font-display uppercase tracking-widest text-text-tertiary mb-2">Suggested Fix</h4>
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
            {analysis.product_bug_report && typeof analysis.product_bug_report === 'object' && (
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
                  {providers.length > 0 && (
                    <Select value={selectedProvider} onValueChange={setSelectedProvider}>
                      <SelectTrigger className="h-8 w-24 text-xs">
                        <SelectValue placeholder="Provider" />
                      </SelectTrigger>
                      <SelectContent>
                        {providers.map((p) => (
                          <SelectItem key={p} value={p}>{p}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                  {models.length > 0 && (
                    <Select value={selectedModel} onValueChange={setSelectedModel}>
                      <SelectTrigger className="h-8 w-44 text-xs">
                        <SelectValue placeholder="Model" />
                      </SelectTrigger>
                      <SelectContent>
                        {models.map((m) => (
                          <SelectItem key={m} value={m}>{m}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
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
              {classification === 'CODE ISSUE' && githubAvailable && (
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
