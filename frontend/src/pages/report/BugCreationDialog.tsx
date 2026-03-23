import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { LoadingSpinner } from '@/components/shared/LoadingSpinner'
import { CheckCircle2, ExternalLink, AlertTriangle } from 'lucide-react'
import type { PreviewIssueResponse, CreateIssueResponse, SimilarIssue, CommentsAndReviews, CommentEnrichment } from '@/types'
import { useReportDispatch } from './ReportContext'

type BugTarget = 'github' | 'jira'
type Phase = 'idle' | 'loading' | 'preview' | 'creating' | 'success' | 'error'

interface BugCreationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  jobId: string
  testName: string
  target: BugTarget
  childJobName?: string
  childBuildNumber?: number
  aiProvider?: string
  aiModel?: string
  includeLinks?: boolean
}

export function BugCreationDialog({
  open,
  onOpenChange,
  jobId,
  testName,
  target,
  childJobName,
  childBuildNumber,
  includeLinks = false,
  aiProvider,
  aiModel,
}: BugCreationDialogProps) {
  const dispatch = useReportDispatch()
  const [phase, setPhase] = useState<Phase>('idle')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [similar, setSimilar] = useState<SimilarIssue[]>([])
  const [createdUrl, setCreatedUrl] = useState('')
  const [errorMsg, setErrorMsg] = useState('')

  const previewPath = target === 'github' ? 'preview-github-issue' : 'preview-jira-bug'
  const createPath = target === 'github' ? 'create-github-issue' : 'create-jira-bug'
  const label = target === 'github' ? 'GitHub Issue' : 'Jira Bug'

  // Load preview when dialog opens
  useEffect(() => {
    if (!open || phase !== 'idle') return
    setPhase('loading')
    api
      .post<PreviewIssueResponse>(`/results/${jobId}/${previewPath}`, {
        test_name: testName,
        include_links: includeLinks,
        ai_provider: aiProvider ?? '',
        ai_model: aiModel ?? '',
        child_job_name: childJobName ?? '',
        child_build_number: childBuildNumber ?? 0,
      })
      .then((res) => {
        setTitle(res.title)
        setBody(res.body)
        setSimilar(res.similar_issues ?? [])
        setPhase('preview')
      })
      .catch((err) => {
        setErrorMsg(err instanceof Error ? err.message : 'Preview failed')
        setPhase('error')
      })
  }, [open, phase, jobId, previewPath, testName, includeLinks, aiProvider, aiModel, childJobName, childBuildNumber])

  async function handleCreate() {
    setPhase('creating')
    try {
      const res = await api.post<CreateIssueResponse>(`/results/${jobId}/${createPath}`, {
        test_name: testName,
        title,
        body,
        child_job_name: childJobName ?? '',
        child_build_number: childBuildNumber ?? 0,
      })
      setCreatedUrl(res.url)
      setPhase('success')

      // Refresh comments + enrichments — backend auto-added a comment with the issue link
      api.get<CommentsAndReviews>(`/results/${jobId}/comments`)
        .then((r) => dispatch({ type: 'SET_COMMENTS_AND_REVIEWS', payload: r }))
        .catch(() => {})
      api.post<{ enrichments: Record<string, CommentEnrichment[]> }>(`/results/${jobId}/enrich-comments`)
        .then((r) => dispatch({ type: 'SET_ENRICHMENTS', payload: r.enrichments ?? {} }))
        .catch(() => {})
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Creation failed')
      setPhase('error')
    }
  }

  function handleClose() {
    onOpenChange(false)
    setTimeout(() => {
      setPhase('idle')
      setTitle('')
      setBody('')
      setSimilar([])
      setCreatedUrl('')
      setErrorMsg('')
    }, 200)
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{phase === 'success' ? `${label} Created` : `Create ${label}`}</DialogTitle>
          {phase === 'preview' && <DialogDescription>Review and edit before creating.</DialogDescription>}
        </DialogHeader>

        {/* Loading */}
        {phase === 'loading' && (
          <div className="flex flex-col items-center gap-4 py-8">
            <LoadingSpinner size="lg" />
            <p className="text-sm text-text-secondary">Generating preview...</p>
          </div>
        )}

        {/* Preview */}
        {phase === 'preview' && (
          <div className="space-y-4">
            {similar.length > 0 && (
              <div className="rounded-md border border-signal-orange/30 bg-glow-orange p-3">
                <div className="flex items-center gap-2 text-sm font-medium text-signal-orange">
                  <AlertTriangle className="h-4 w-4" />
                  {similar.length} similar {similar.length === 1 ? 'issue' : 'issues'} found
                </div>
                <ul className="mt-2 space-y-1">
                  {similar.map((s) => (
                    <li key={s.url || s.key} className="text-xs">
                      <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline">
                        {s.key || `#${s.number}`}: {s.title}
                      </a>
                      {s.status && (
                        <Badge variant="outline" className="ml-2 text-[10px]">
                          {s.status}
                        </Badge>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="space-y-2">
              <label className="text-xs font-display uppercase tracking-widest text-text-tertiary">Title</label>
              <Input value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-display uppercase tracking-widest text-text-tertiary">Body</label>
              <Textarea value={body} onChange={(e) => setBody(e.target.value)} rows={12} className="font-mono text-xs" />
            </div>
          </div>
        )}

        {/* Creating */}
        {phase === 'creating' && (
          <div className="flex flex-col items-center gap-4 py-8">
            <LoadingSpinner size="lg" />
            <p className="text-sm text-text-secondary">Creating {label.toLowerCase()}...</p>
          </div>
        )}

        {/* Success */}
        {phase === 'success' && (
          <div className="flex flex-col items-center gap-4 py-8 animate-scale-in">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-signal-green/15">
              <CheckCircle2 className="h-8 w-8 text-signal-green" />
            </div>
            <p className="text-sm text-text-secondary">{label} created successfully.</p>
            <a
              href={createdUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-sm text-text-link hover:underline"
            >
              Open {label} <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
        )}

        {/* Error */}
        {phase === 'error' && (
          <div className="flex flex-col items-center gap-4 py-8">
            <p className="text-sm text-signal-red">{errorMsg}</p>
          </div>
        )}

        <DialogFooter>
          {phase === 'preview' && (
            <>
              <Button variant="ghost" onClick={handleClose}>Cancel</Button>
              <Button onClick={handleCreate} disabled={!title.trim()}>Create {label}</Button>
            </>
          )}
          {(phase === 'success' || phase === 'error') && (
            <Button variant="ghost" onClick={handleClose}>Close</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
