export const CLASSIFICATIONS = [
  'CODE ISSUE',
  'PRODUCT BUG',
  'FLAKY',
  'REGRESSION',
  'INFRASTRUCTURE',
  'KNOWN_BUG',
  'INTERMITTENT',
] as const

export type Classification = (typeof CLASSIFICATIONS)[number]
