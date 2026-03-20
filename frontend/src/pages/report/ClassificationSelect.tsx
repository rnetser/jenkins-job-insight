import { useState } from 'react'
import { api } from '@/lib/api'
import { useReportDispatch } from './ReportContext'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

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

  async function handleChange(value: string) {
    if (value === currentClassification) return
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
    } finally {
      setLoading(false)
    }
  }

  return (
    <Select value={currentClassification} onValueChange={handleChange} disabled={loading}>
      <SelectTrigger className="h-8 w-40 text-xs">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="CODE ISSUE">CODE ISSUE</SelectItem>
        <SelectItem value="PRODUCT BUG">PRODUCT BUG</SelectItem>
      </SelectContent>
    </Select>
  )
}
