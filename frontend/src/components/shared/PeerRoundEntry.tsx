import { useState } from 'react'
import type { PeerRound } from '@/types'
import { Badge } from '@/components/ui/badge'
import { ClassificationBadge } from '@/components/shared/ClassificationBadge'

interface PeerRoundEntryProps {
  entry: PeerRound
  compact?: boolean
}

export function PeerRoundEntry({ entry, compact = false }: PeerRoundEntryProps) {
  const [detailsExpanded, setDetailsExpanded] = useState(false)
  const isOrchestrator = entry.role === 'orchestrator'

  const TRUNCATE_LIMIT = 200
  const needsTruncation = compact && (entry.details?.length ?? 0) > TRUNCATE_LIMIT
  const displayDetails = needsTruncation && !detailsExpanded
    ? entry.details.slice(0, TRUNCATE_LIMIT) + '...'
    : entry.details

  return (
    <div className="flex flex-col gap-1.5 rounded-md bg-surface-elevated p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={isOrchestrator ? 'default' : 'purple'} className="text-[10px]">
          {isOrchestrator ? (compact ? 'Main AI' : 'Orchestrator') : 'Peer'}
        </Badge>
        <span className="font-mono text-[10px] text-text-tertiary">
          {entry.ai_provider}/{entry.ai_model}
        </span>
        <ClassificationBadge classification={entry.classification} className="text-[10px]" />
        {!isOrchestrator && entry.agrees_with_orchestrator !== null && (
          <Badge variant={entry.agrees_with_orchestrator ? 'success' : 'destructive'} className="text-[10px]">
            {entry.agrees_with_orchestrator ? 'Agrees' : 'Disagrees'}
          </Badge>
        )}
      </div>
      {entry.details && (
        <div>
          <p className="text-xs text-text-secondary whitespace-pre-wrap">{displayDetails}</p>
          {needsTruncation && (
            <button
              type="button"
              className="text-[10px] text-text-link hover:underline mt-0.5"
              aria-expanded={detailsExpanded}
              onClick={() => setDetailsExpanded(!detailsExpanded)}
            >
              {detailsExpanded ? 'Show less' : 'Show more'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
