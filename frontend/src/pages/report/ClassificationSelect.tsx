import { useState } from 'react'
import { api } from '@/lib/api'
import { useReportDispatch } from './ReportContext'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { CLASSIFICATIONS } from '@/constants/classifications'

interface ClassificationSelectProps {
  jobId: string
  testName: string
  currentClassification: string
  childJobName?: string
  childBuildNumber?: number
}

export function ClassificationSelect({
  jobId,
  testName,
  currentClassification,
  childJobName,
  childBuildNumber,
}: ClassificationSelectProps) {
  const dispatch = useReportDispatch()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleChange(value: string) {
    if (value === currentClassification) return
    setError(null)
    setLoading(true)
    try {
      await api.put(`/results/${jobId}/override-classification`, {
        test_name: testName,
        classification: value,
        child_job_name: childJobName ?? '',
        child_build_number: childBuildNumber ?? 0,
      })
      dispatch({
        type: 'OVERRIDE_CLASSIFICATION',
        payload: { testName, classification: value, childJobName, childBuildNumber },
      })
    } catch (err) {
      console.error('Failed to save classification:', err)
      setError('Failed to save')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center gap-1">
      <Select value={currentClassification} onValueChange={handleChange} disabled={loading}>
        <SelectTrigger className="h-8 w-40 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {CLASSIFICATIONS.map((c) => (
            <SelectItem key={c} value={c}>
              {c}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error && <span className="text-signal-red text-xs">{error}</span>}
    </div>
  )
}
