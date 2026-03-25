/**
 * Build a URL-safe hash fragment identifier for a child job.
 * Format: "child-<jobname>-<buildnumber>"
 */
export function childJobHashId(jobName: string, buildNumber: number): string {
  return `child-${encodeURIComponent(jobName)}-${buildNumber}`
}
