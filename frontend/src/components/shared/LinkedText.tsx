import { useMemo, type ReactNode } from 'react'
import { autoLinkAnalysis, type RepoUrl, type LinkSegment } from '@/lib/autoLink'

interface LinkedTextProps {
  text: string
  repoUrls: RepoUrl[]
  /** Custom renderer for link segments. Must return a React element with a stable `key` (typically the `index` parameter). */
  renderLink?: (seg: LinkSegment, index: number) => ReactNode
}

function isSafeHref(href: string): boolean {
  try {
    const url = new URL(href, 'https://example.invalid')
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

export function LinkedText({ text, repoUrls, renderLink }: LinkedTextProps) {
  const segments = useMemo<LinkSegment[]>(() => autoLinkAnalysis(text, repoUrls), [text, repoUrls])

  return (
    <>
      {segments.map((seg, i) =>
        seg.type === 'link' && isSafeHref(seg.href!) ? (
          renderLink ? renderLink(seg, i) : (
            <a key={i} href={seg.href} target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline">
              {seg.text}
            </a>
          )
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
    </>
  )
}
