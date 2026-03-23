import type { FailureAnalysis, GroupedFailure } from '@/types'

/** Check whether a comment belongs to the given test-group scope. */
export function isCommentInScope(
  comment: { test_name: string; child_job_name?: string; child_build_number?: number },
  groupTestNames: string[] | Set<string>,
  childJobName?: string,
  childBuildNumber?: number,
): boolean {
  const names = groupTestNames instanceof Set ? groupTestNames : new Set(groupTestNames)
  if (!names.has(comment.test_name)) return false
  if (childJobName) return comment.child_job_name === childJobName && comment.child_build_number === childBuildNumber
  return !comment.child_job_name
}

/** Compute grouping key — matches Python _grouping_key(). */
export function groupingKey(failure: FailureAnalysis): string {
  return failure.error_signature || `unique-${failure.test_name}`
}

/** Group failures by error signature, preserving order. */
export function groupFailures(
  failures: FailureAnalysis[],
  prefix = '',
): GroupedFailure[] {
  const orderMap = new Map<string, number>()
  const groupMap = new Map<string, FailureAnalysis[]>()

  for (const f of failures) {
    const key = groupingKey(f)
    if (!groupMap.has(key)) {
      orderMap.set(key, orderMap.size)
      groupMap.set(key, [])
    }
    groupMap.get(key)!.push(f)
  }

  const groups: GroupedFailure[] = []
  for (const [signature, tests] of groupMap) {
    groups.push({
      signature,
      tests,
      count: tests.length,
      id: prefix ? `${prefix}-${orderMap.get(signature)}` : `group-${orderMap.get(signature)}`,
    })
  }
  return groups
}
