import { useMemo } from 'react'
import type { ChildJobAnalysis } from '@/types'
import { useSessionState } from '@/lib/useSessionState'
import { groupFailures } from '@/lib/grouping'
import { FailureCard } from './FailureCard'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, ChevronRight, ExternalLink, GitFork } from 'lucide-react'

interface ChildJobSectionProps {
  child: ChildJobAnalysis
  jobId: string
  depth?: number
}

export function ChildJobSection({ child, jobId, depth = 0 }: ChildJobSectionProps) {
  const expandKey = `jji-expand-${jobId}-child-${child.job_name}-${child.build_number}`
  const [expanded, setExpanded] = useSessionState(expandKey, false)
  const groups = useMemo(
    () => groupFailures(child.failures, `child-${child.job_name}-${child.build_number}`),
    [child.failures, child.job_name, child.build_number]
  )

  return (
    <div className={depth > 0 ? 'ml-4 border-l-2 border-border-muted pl-4' : ''}>
      {/* Header */}
      <button
        className="flex w-full items-center gap-3 rounded-md bg-surface-elevated/50 px-4 py-3 text-left mb-4"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-text-tertiary" /> : <ChevronRight className="h-4 w-4 shrink-0 text-text-tertiary" />}
        <GitFork className="h-4 w-4 text-signal-blue shrink-0" />
        <div className="min-w-0 flex-1">
          <span className="font-display text-sm font-semibold text-text-primary">{child.job_name}</span>
          <span className="ml-2 font-mono text-xs text-text-tertiary">#{child.build_number}</span>
        </div>
        <Badge variant="outline" className="shrink-0">
          {child.failures.length} {child.failures.length === 1 ? 'failure' : 'failures'}
        </Badge>
        {child.jenkins_url && (
          <a
            href={child.jenkins_url}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-text-tertiary hover:text-text-link"
            onClick={(e) => e.stopPropagation()}
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        )}
      </button>

      {expanded && (
        <>
          {child.summary && (
            <div className="mb-4 rounded-md bg-glow-blue border border-signal-blue/20 p-3 text-sm text-text-secondary">
              {child.summary}
            </div>
          )}

          {child.note && <div className="mb-4 text-xs text-signal-orange">{child.note}</div>}

          <div className="space-y-3">
            {groups.map((g, i) => (
              <FailureCard
                key={g.id}
                group={g}
                jobId={jobId}
                childJobName={child.job_name}
                childBuildNumber={child.build_number}
                index={i}
              />
            ))}
          </div>

          {child.failed_children.length > 0 && (
            <div className="mt-4 space-y-4">
              {child.failed_children.map((nested) => (
                <ChildJobSection
                  key={`${nested.job_name}-${nested.build_number}`}
                  child={nested}
                  jobId={jobId}
                  depth={depth + 1}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
