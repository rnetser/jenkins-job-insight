import { useState } from 'react'
import { api } from '@/lib/api'
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
  const reviewed = reviews[key]?.reviewed ?? false

  async function toggle() {
    setLoading(true)
    try {
      await api.put(`/results/${jobId}/reviewed`, {
        test_name: testName,
        reviewed: !reviewed,
        child_job_name: childJobName ?? '',
        child_build_number: childBuildNumber ?? 0,
      })
      dispatch({
        type: 'SET_REVIEW',
        payload: { key, state: { reviewed: !reviewed, updated_at: new Date().toISOString() } },
      })
    } catch (err) {
      console.error('Failed to toggle review status:', err)
    } finally {
      setLoading(false)
    }
  }

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
      title={reviewed ? 'Mark as unreviewed' : 'Mark as reviewed'}
    >
      <CheckCircle2 className="h-3.5 w-3.5" />
      {reviewed ? 'Reviewed' : 'Review'}
    </button>
  )
}
