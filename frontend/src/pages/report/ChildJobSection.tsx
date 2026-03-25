import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { ChildJobAnalysis } from '@/types'
import { useSessionState } from '@/lib/useSessionState'
import { groupFailures } from '@/lib/grouping'
import { useExpandCollapseAll } from '@/lib/useExpandCollapseAll'
import { childJobHashId } from '@/lib/childJobHash'
import { FailureCard } from './FailureCard'
import { Badge } from '@/components/ui/badge'
import { ExpandCollapseButtons } from '@/components/shared/ExpandCollapseButtons'
import { ChevronDown, ChevronRight, ExternalLink, GitFork } from 'lucide-react'

interface ChildJobSectionProps {
  child: ChildJobAnalysis
  jobId: string
  depth?: number
  /** Hash fragment (without #) from the URL, used for auto-expand on load. */
  activeHash?: string
}

export function ChildJobSection({ child, jobId, depth = 0, activeHash }: ChildJobSectionProps) {
  const expandKey = `jji-expand-${jobId}-child-${child.job_name}-${child.build_number}`
  const hashId = childJobHashId(child.job_name, child.build_number)
  const sectionRef = useRef<HTMLDivElement>(null)
  const [expanded, setExpanded] = useSessionState(expandKey, false)

  // Auto-expand and scroll when the URL hash targets this child job
  useEffect(() => {
    if (activeHash && activeHash === hashId && !expanded) {
      setExpanded(true)
      // Scroll after DOM updates
      requestAnimationFrame(() => {
        sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    }
  }, [activeHash, hashId, expanded, setExpanded])

  const handleToggle = useCallback(() => {
    const next = !expanded
    setExpanded(next)
    if (next) {
      // Set hash without navigation
      history.replaceState(null, '', `#${hashId}`)
    } else {
      // Clear hash
      history.replaceState(null, '', window.location.pathname + window.location.search)
    }
  }, [expanded, setExpanded, hashId])

  const groups = useMemo(
    () => groupFailures(child.failures, `child-${child.job_name}-${child.build_number}`),
    [child.failures, child.job_name, child.build_number]
  )

  // Expand/collapse all failure cards within this child job
  const getFailureKeys = useCallback(
    () => groups.map((g) => `jji-expand-${jobId}-${g.id}`),
    [groups, jobId],
  )
  const { remountKey: failureRemountKey, expandAll: expandAllFailures, collapseAll: collapseAllFailures } =
    useExpandCollapseAll(getFailureKeys)

  return (
    <div ref={sectionRef} id={hashId} className={depth > 0 ? 'ml-4 border-l-2 border-border-muted pl-4' : ''}>
      {/* Header */}
      <div className="flex w-full items-center gap-3 rounded-md bg-surface-elevated/50 px-4 py-3 text-left mb-4">
        <button
          type="button"
          className="flex items-center gap-3 min-w-0 flex-1 bg-transparent border-none p-0 text-left cursor-pointer"
          aria-expanded={expanded}
          onClick={handleToggle}
        >
          {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-text-tertiary" /> : <ChevronRight className="h-4 w-4 shrink-0 text-text-tertiary" />}
          <GitFork className="h-4 w-4 text-signal-blue shrink-0" />
          <div className="min-w-0 flex-1">
            <span className="font-display text-sm font-semibold text-text-primary">{child.job_name}</span>
            <span className="ml-2 font-mono text-xs text-text-tertiary">#{child.build_number}</span>
          </div>
        </button>
        <Badge variant="outline" className="shrink-0">
          {child.failures.length} {child.failures.length === 1 ? 'failure' : 'failures'}
        </Badge>
        {child.jenkins_url && (
          <a
            href={child.jenkins_url}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-text-tertiary hover:text-text-link"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        )}
      </div>

      {expanded && (
        <div className="ml-6 border-l-2 border-border-default/30 pl-4 mt-2 space-y-3">
          {child.summary && (
            <div className="rounded-md bg-glow-blue border border-signal-blue/20 p-3 text-sm text-text-secondary">
              {child.summary}
            </div>
          )}

          {child.note && <div className="text-xs text-signal-orange">{child.note}</div>}

          {groups.length >= 2 && (
            <div className="flex justify-end">
              <ExpandCollapseButtons onExpandAll={expandAllFailures} onCollapseAll={collapseAllFailures} />
            </div>
          )}

          <div className="space-y-3" key={failureRemountKey}>
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
            <div className="mt-1 space-y-3">
              {child.failed_children.map((nested) => (
                <ChildJobSection
                  key={`${nested.job_name}-${nested.build_number}`}
                  child={nested}
                  jobId={jobId}
                  depth={depth + 1}
                  activeHash={activeHash}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
