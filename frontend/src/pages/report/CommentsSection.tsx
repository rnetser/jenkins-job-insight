import { useState, useRef, useEffect } from 'react'
import { api } from '@/lib/api'
import { formatTimestamp } from '@/lib/utils'
import { isCommentInScope } from '@/lib/grouping'
import { getUsername } from '@/lib/cookies'
import { useReportState, useReportDispatch, useRefreshEnrichments } from './ReportContext'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Trash2, MessageSquare } from 'lucide-react'
import type { Comment } from '@/types'

/* ------------------------------------------------------------------ */
/*  Auto-link: convert URLs in comment text to named clickable links   */
/* ------------------------------------------------------------------ */

const GITHUB_PR_RE = /https?:\/\/github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)\S*/g
const GITHUB_ISSUE_RE = /https?:\/\/github\.com\/([^/]+\/[^/]+)\/issues\/(\d+)\S*/g
const JIRA_BROWSE_RE = /https?:\/\/[^/]+\/browse\/([A-Z][A-Z0-9]+-\d+)\S*/g
const GENERIC_URL_RE = /https?:\/\/\S+/g

function trimTrailingPunctuation(url: string): string {
  let result = url
  while (result.length > 0) {
    const last = result[result.length - 1]
    if (last === '>') {
      result = result.slice(0, -1)
      continue
    }
    if (last === ')') {
      const opens = (result.match(/\(/g) || []).length
      const closes = (result.match(/\)/g) || []).length
      if (closes > opens) {
        result = result.slice(0, -1)
        continue
      }
      break
    }
    if (/[.,;:!?]/.test(last)) {
      result = result.slice(0, -1)
      continue
    }
    break
  }
  return result
}

type LinkMatch = { start: number; end: number; text: string; href: string }

interface LinkSegment {
  type: 'text' | 'link'
  text: string
  href?: string
}

function collectMatches(
  raw: string,
  pattern: RegExp,
  matches: LinkMatch[],
  textFn: (m: RegExpMatchArray) => string,
) {
  for (const m of raw.matchAll(pattern)) {
    const start = m.index!
    const href = trimTrailingPunctuation(m[0])
    const end = start + href.length
    const text = textFn(m)
    matches.push({ start, end, text, href })
  }
}

/** Remove overlapping matches, preferring earlier entries (specific patterns added first). */
function deduplicateMatches(matches: LinkMatch[]): LinkMatch[] {
  const sorted = [...matches].sort((a, b) => a.start - b.start)
  const result: typeof matches = []
  for (const m of sorted) {
    if (result.length > 0 && m.start < result[result.length - 1].end) continue
    result.push(m)
  }
  return result
}

function autoLinkComment(raw: string): LinkSegment[] {
  // Collect all link matches with their positions
  const matches: LinkMatch[] = []

  collectMatches(raw, GITHUB_PR_RE, matches, (m) => `${m[1]}#${m[2]}`)
  collectMatches(raw, GITHUB_ISSUE_RE, matches, (m) => `${m[1]}#${m[2]}`)
  collectMatches(raw, JIRA_BROWSE_RE, matches, (m) => m[1])
  collectMatches(raw, GENERIC_URL_RE, matches, (m) => trimTrailingPunctuation(m[0]))

  const deduplicated = deduplicateMatches(matches)

  // Build segments
  const segments: LinkSegment[] = []
  let cursor = 0
  for (const m of deduplicated) {
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
  const canPost = testNames.length === 1
  const primaryTestName = canPost ? testNames[0] : null
  const scopedChildJobName = childJobName ?? ''
  const scopedChildBuildNumber = childBuildNumber ?? 0

  const { comments, enrichments } = useReportState()
  const dispatch = useReportDispatch()
  const refreshEnrichments = useRefreshEnrichments()
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const draftActiveRef = useRef(false)
  const username = getUsername()

  /** Centralized draft-count state machine: syncs draftActiveRef with the global count. */
  const syncDraftActive = (hasContent: boolean) => {
    if (hasContent && !draftActiveRef.current) {
      draftActiveRef.current = true
      dispatch({ type: 'INCREMENT_DRAFT_COUNT' })
    } else if (!hasContent && draftActiveRef.current) {
      draftActiveRef.current = false
      dispatch({ type: 'DECREMENT_DRAFT_COUNT' })
    }
  }

  // Sync draft-active state whenever text changes (including after submit clears it).
  // This replaces the previous side-effect inside the setText updater, keeping updaters pure.
  useEffect(() => {
    syncDraftActive(canPost && text.trim().length > 0)
  }, [canPost, text]) // eslint-disable-line react-hooks/exhaustive-deps -- syncDraftActive is stable via ref

  // Ensure the draft count is decremented if this editor unmounts while active.
  useEffect(() => {
    return () => {
      if (draftActiveRef.current) {
        dispatch({ type: 'DECREMENT_DRAFT_COUNT' })
      }
    }
  }, [dispatch])

  const testComments = comments.filter((c) => isCommentInScope(c, testNames, scopedChildJobName, scopedChildBuildNumber))

  async function handleSubmit() {
    if (submitting) return
    if (!canPost || !primaryTestName) {
      setSubmitError('Posting comments from grouped failures is not supported yet.')
      return
    }
    const submittedDraft = text
    const submittedText = submittedDraft.trim()
    if (!submittedText) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const res = await api.post<{ id: number }>(`/results/${jobId}/comments`, {
        test_name: primaryTestName,
        comment: submittedText,
        child_job_name: scopedChildJobName,
        child_build_number: scopedChildBuildNumber,
      })
      const fresh: Comment = {
        id: res.id,
        job_id: jobId,
        test_name: primaryTestName,
        child_job_name: scopedChildJobName,
        child_build_number: scopedChildBuildNumber,
        comment: submittedText,
        username,
        created_at: new Date().toISOString(),
      }
      dispatch({ type: 'ADD_COMMENT', payload: fresh })
      // Refresh enrichments to pick up any tracker links in the new comment
      refreshEnrichments(jobId)
      setText((current) => (current === submittedDraft ? '' : current))
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to post comment')
    } finally {
      setSubmitting(false)
    }
  }

  const [deletingIds, setDeletingIds] = useState<Set<number>>(new Set())

  async function handleDelete(id: number) {
    if (deletingIds.has(id)) return
    setSubmitError(null)
    setDeletingIds((prev) => new Set(prev).add(id))
    try {
      await api.delete(`/results/${jobId}/comments/${id}`)
      dispatch({ type: 'REMOVE_COMMENT', payload: id })
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to delete comment')
    } finally {
      setDeletingIds((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
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
                      {formatTimestamp(c.created_at)}
                    </span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-text-secondary">
                    {segments.map((seg, i) => {
                      if (seg.type === 'link') {
                        // Find matching enrichment for this link
                        const match = badges.find((b) => seg.text === b.key || seg.href === b.key)
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
                {username && c.username === username && (
                  <button
                    type="button"
                    aria-label="Delete comment"
                    onClick={() => handleDelete(c.id)}
                    disabled={deletingIds.has(c.id)}
                    className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal-blue disabled:opacity-50"
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

      <div className="space-y-1">
        {canPost ? (
          <div className="flex gap-2">
            <Textarea
              aria-label="Add a comment"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Add a comment..."
              className="min-h-[36px] resize-none text-sm"
              rows={1}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing) return
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
        ) : (
          <span className="text-xs text-text-tertiary">
            Posting comments from grouped failures is not supported yet.
          </span>
        )}
        {submitError && (
          <span role="alert" className="text-signal-red text-xs">
            {submitError}
          </span>
        )}
      </div>
    </div>
  )
}
