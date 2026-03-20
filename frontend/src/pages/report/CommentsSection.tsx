import { useState } from 'react'
import { api } from '@/lib/api'
import { getUsername } from '@/lib/cookies'
import { useReportState, useReportDispatch } from './ReportContext'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Trash2, MessageSquare } from 'lucide-react'
import type { Comment, CommentEnrichment } from '@/types'

/* ------------------------------------------------------------------ */
/*  Auto-link: convert URLs in comment text to named clickable links   */
/* ------------------------------------------------------------------ */

const GITHUB_PR_RE = /https?:\/\/github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)\S*/g
const GITHUB_ISSUE_RE = /https?:\/\/github\.com\/([^/]+\/[^/]+)\/issues\/(\d+)\S*/g
const JIRA_BROWSE_RE = /https?:\/\/[^/]+\/browse\/([A-Z][A-Z0-9]+-\d+)\S*/g
const GENERIC_URL_RE = /https?:\/\/\S+/g

interface LinkSegment {
  type: 'text' | 'link'
  text: string
  href?: string
}

function autoLinkComment(raw: string): LinkSegment[] {
  // Collect all link matches with their positions
  const matches: { start: number; end: number; text: string; href: string }[] = []

  for (const m of raw.matchAll(GITHUB_PR_RE)) {
    matches.push({ start: m.index, end: m.index + m[0].length, text: `${m[1]}#${m[2]}`, href: m[0] })
  }
  for (const m of raw.matchAll(GITHUB_ISSUE_RE)) {
    if (matches.some((e) => e.start === m.index)) continue
    matches.push({ start: m.index, end: m.index + m[0].length, text: `${m[1]}#${m[2]}`, href: m[0] })
  }
  for (const m of raw.matchAll(JIRA_BROWSE_RE)) {
    // Skip if already captured by a longer match at the same position
    if (matches.some((e) => e.start === m.index)) continue
    matches.push({ start: m.index, end: m.index + m[0].length, text: m[1], href: m[0] })
  }
  for (const m of raw.matchAll(GENERIC_URL_RE)) {
    if (matches.some((e) => e.start === m.index)) continue
    matches.push({ start: m.index, end: m.index + m[0].length, text: m[0], href: m[0] })
  }

  matches.sort((a, b) => a.start - b.start)

  // Build segments
  const segments: LinkSegment[] = []
  let cursor = 0
  for (const m of matches) {
    if (m.start > cursor) segments.push({ type: 'text', text: raw.slice(cursor, m.start) })
    segments.push({ type: 'link', text: m.text, href: m.href })
    cursor = m.end
  }
  if (cursor < raw.length) segments.push({ type: 'text', text: raw.slice(cursor) })

  return segments.length > 0 ? segments : [{ type: 'text', text: raw }]
}

/* ------------------------------------------------------------------ */
/*  Enrichment status badge colors                                     */
/* ------------------------------------------------------------------ */

function enrichmentBadgeVariant(status: string): 'success' | 'destructive' | 'default' {
  const s = status.toLowerCase()
  if (s === 'merged' || s === 'open') return 'success'
  if (s === 'closed') return 'destructive'
  return 'default'
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

interface CommentsSectionProps {
  jobId: string
  testNames: string[]
  childJobName?: string
  childBuildNumber?: number
}

export function CommentsSection({ jobId, testNames, childJobName, childBuildNumber }: CommentsSectionProps) {
  const { comments, enrichments } = useReportState()
  const dispatch = useReportDispatch()
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const username = getUsername()

  const testComments = comments.filter((c) => {
    if (!testNames.includes(c.test_name)) return false
    if (childJobName) return c.child_job_name === childJobName && c.child_build_number === childBuildNumber
    return !c.child_job_name
  })

  async function handleSubmit() {
    if (!text.trim()) return
    setSubmitting(true)
    try {
      const res = await api.post<{ id: number }>(`/results/${jobId}/comments`, {
        test_name: testNames[0],
        comment: text.trim(),
        child_job_name: childJobName ?? '',
        child_build_number: childBuildNumber ?? 0,
      })
      const fresh: Comment = {
        id: res.id,
        job_id: jobId,
        test_name: testNames[0],
        child_job_name: childJobName ?? '',
        child_build_number: childBuildNumber ?? 0,
        comment: text.trim(),
        username,
        created_at: new Date().toISOString(),
      }
      dispatch({ type: 'ADD_COMMENT', payload: fresh })
      // Refresh enrichments to pick up any tracker links in the new comment
      api.post<{ enrichments: Record<string, CommentEnrichment[]> }>(`/results/${jobId}/enrich-comments`)
        .then((res) => dispatch({ type: 'SET_ENRICHMENTS', payload: res.enrichments ?? {} }))
        .catch(() => {})
      setText('')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDelete(id: number) {
    try {
      await api.delete(`/results/${jobId}/comments/${id}`)
      dispatch({ type: 'REMOVE_COMMENT', payload: id })
    } catch {
      /* UI-courtesy delete — swallow */
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs font-display uppercase tracking-widest text-text-tertiary">
        <MessageSquare className="h-3.5 w-3.5" />
        Comments ({testComments.length})
      </div>

      {testComments.length > 0 && (
        <div className="space-y-2">
          {testComments.map((c) => {
            const segments = autoLinkComment(c.comment)
            const badges = enrichments[String(c.id)] ?? []

            return (
              <div
                key={c.id}
                className="group flex items-start gap-3 rounded-md bg-surface-elevated/50 px-3 py-2 text-sm animate-slide-up"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-signal-blue">{c.username || 'anon'}</span>
                    <span className="text-[10px] text-text-tertiary">
                      {new Date(c.created_at).toLocaleString()}
                    </span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-text-secondary">
                    {segments.map((seg, i) => {
                      if (seg.type === 'link') {
                        // Find matching enrichment for this link
                        const match = badges.find((b) => seg.text.includes(b.key) || b.key.includes(seg.text))
                        return (
                          <span key={i} className="inline-flex items-center gap-1">
                            <a href={seg.href} target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline">
                              {seg.text}
                            </a>
                            {match && (
                              <Badge variant={enrichmentBadgeVariant(match.status)} className="text-[10px] align-middle">
                                {match.status}
                              </Badge>
                            )}
                          </span>
                        )
                      }
                      return <span key={i}>{seg.text}</span>
                    })}
                  </p>
                </div>
                {c.username === username && (
                  <button
                    onClick={() => handleDelete(c.id)}
                    className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                    title="Delete comment"
                  >
                    <Trash2 className="h-3.5 w-3.5 text-text-tertiary hover:text-signal-red" />
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}

      <div className="flex gap-2">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Add a comment..."
          className="min-h-[36px] resize-none text-sm"
          rows={1}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSubmit()
            }
          }}
        />
        <Button size="sm" onClick={handleSubmit} disabled={!text.trim() || submitting} className="shrink-0">
          Post
        </Button>
      </div>
    </div>
  )
}
