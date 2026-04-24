import { Zap } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { TokenUsageSummary } from '@/types'

/** Format a number with K/M suffixes (e.g. 15000 → "15K", 1500000 → "1.5M"). */
export function formatCompactNumber(n: number): string {
  if (n >= 1_000_000) {
    const m = n / 1_000_000
    return m % 1 === 0 ? `${m}M` : `${m.toFixed(1)}M`
  }
  if (n >= 1_000) {
    const k = n / 1_000
    return k % 1 === 0 ? `${k}K` : `${k.toFixed(1)}K`
  }
  return String(n)
}

/** Format USD cost for display. */
function formatCost(cost: number | null): string | null {
  if (cost == null || cost === 0) return null
  return `$${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(2)}`
}

interface TokenUsageBadgeProps {
  usage: TokenUsageSummary
}

export function TokenUsageBadge({ usage }: TokenUsageBadgeProps) {
  const costStr = formatCost(usage.total_cost_usd)

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center gap-1 rounded-full bg-surface-elevated px-2.5 py-0.5 text-[10px] font-mono text-text-tertiary">
          <Zap className="h-3 w-3" />
          {formatCompactNumber(usage.total_input_tokens)} in / {formatCompactNumber(usage.total_output_tokens)} out
          {costStr && <> · {costStr}</>}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <div className="space-y-1 text-xs">
          <p>Total tokens: {usage.total_tokens.toLocaleString()}</p>
          <p>Input: {usage.total_input_tokens.toLocaleString()} · Output: {usage.total_output_tokens.toLocaleString()}</p>
          {usage.total_cache_read_tokens > 0 && <p>Cache read: {usage.total_cache_read_tokens.toLocaleString()}</p>}
          {usage.total_cache_write_tokens > 0 && <p>Cache write: {usage.total_cache_write_tokens.toLocaleString()}</p>}
          <p>API calls: {usage.total_calls}</p>
          {usage.total_duration_ms > 0 && <p>Duration: {(usage.total_duration_ms / 1000).toFixed(1)}s</p>}
          {costStr && <p>Cost: {costStr}</p>}
        </div>
      </TooltipContent>
    </Tooltip>
  )
}
