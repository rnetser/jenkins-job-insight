import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Check if a failed analysis was due to AI timeout. */
export function isAnalysisTimeout(status: string, error?: string | null, summary?: string | null): boolean {
  if (status !== 'failed') return false
  const check = (s?: string | null) => !!s?.toLowerCase().includes('timed out')
  return check(error) || check(summary)
}

/** Format the duration between two dates as a compact string. */
export function formatDuration(start: Date, end: Date): string {
  const diffMs = end.getTime() - start.getTime()
  if (diffMs < 0) return '\u2014'
  const totalSeconds = Math.floor(diffMs / 1000)
  const mins = Math.floor(totalSeconds / 60)
  const secs = totalSeconds % 60
  if (mins === 0) return `${secs}s`
  return `${mins}m ${secs}s`
}

/** Parse a UTC timestamp from the API (SQLite format) into a Date. */
export function parseApiTimestamp(ts: string): Date {
  // Normalize: ensure UTC timezone marker
  let normalized = ts
  if (!ts.includes('T')) {
    normalized = ts.replace(' ', 'T') + 'Z'
  } else if (!/[Zz]$|[+-]\d{2}:\d{2}$|[+-]\d{4}$/.test(ts)) {
    // Has T but no timezone — assume UTC
    normalized = ts + 'Z'
  }
  return new Date(normalized)
}
