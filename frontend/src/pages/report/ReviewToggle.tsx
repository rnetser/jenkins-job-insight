import { useState } from 'react'
import { api } from '@/lib/api'
import { getUsername } from '@/lib/cookies'
import { useReportState, useReportDispatch, reviewKey } from './ReportContext'
import { CheckCircle2 } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ReviewToggleProps {
  jobId: string
  testName: string
  childJobName?: string
  childBuildNumber?: number
}

export function ReviewToggle({ jobId, testName, childJobName, childBuildNumber }: ReviewToggleProps) {
  const { reviews } = useReportState()
  const dispatch = useReportDispatch()
  const [loading, setLoading] = useState(false)

  const key = reviewKey(testName, childJobName, childBuildNumber)
  const reviewState = reviews[key]
  const reviewed = reviewState?.reviewed ?? false
  const reviewedBy = reviewState?.username ?? ''

  async function toggle() {
    setLoading(true)
    try {
      const res = await api.put<{ status: string; reviewed_by: string }>(`/results/${jobId}/reviewed`, {
        test_name: testName,
        reviewed: !reviewed,
        child_job_name: childJobName ?? '',
        child_build_number: childBuildNumber ?? 0,
      })
      const username = res.reviewed_by || getUsername()
      dispatch({
        type: 'SET_REVIEW',
        payload: { key, state: { reviewed: !reviewed, username, updated_at: new Date().toISOString() } },
      })
    } catch (err) {
      console.error('Failed to toggle review status:', err)
    } finally {
      setLoading(false)
    }
  }

  const tooltipText = reviewed
    ? reviewedBy
      ? `Reviewed by ${reviewedBy} — click to unmark`
      : 'Mark as unreviewed'
    : 'Mark as reviewed'

  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        toggle()
      }}
      disabled={loading}
      className={cn(
        'flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors duration-150',
        reviewed
          ? 'bg-signal-green/15 text-signal-green'
          : 'bg-surface-elevated text-text-tertiary hover:text-text-secondary',
      )}
      title={tooltipText}
    >
      <CheckCircle2 className="h-3.5 w-3.5" />
      {reviewed ? 'Reviewed' : 'Review'}
      {reviewed && reviewedBy && (
        <span className="text-signal-green/70">by {reviewedBy}</span>
      )}
    </button>
  )
}
