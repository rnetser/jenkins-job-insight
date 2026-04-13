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
import type { PreviewIssueResponse, CreateIssueResponse, SimilarIssue, CommentsAndReviews } from '@/types'
import { getGithubToken, getJiraToken, getJiraEmail } from '@/lib/cookies'
import { useReportDispatch, useRefreshEnrichments } from './ReportContext'

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
  availableRepos?: Array<{ name: string; url: string }>
  defaultProjectKey?: string
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
  availableRepos,
  defaultProjectKey,
}: BugCreationDialogProps) {
  const dispatch = useReportDispatch()
  const refreshEnrichments = useRefreshEnrichments()
  const [phase, setPhase] = useState<Phase>('idle')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [similar, setSimilar] = useState<SimilarIssue[]>([])
  const [createdUrl, setCreatedUrl] = useState('')
  const [errorMsg, setErrorMsg] = useState('')
  const [selectedRepo, setSelectedRepo] = useState(availableRepos?.[0]?.url ?? '')
  const [jiraProjectKey, setJiraProjectKey] = useState(defaultProjectKey || '')
  const [jiraProjects, setJiraProjects] = useState<Array<{key: string; name: string}>>([])
  const [jiraSecurityLevel, setJiraSecurityLevel] = useState('')

  const previewPath = target === 'github' ? 'preview-github-issue' : 'preview-jira-bug'
  const createPath = target === 'github' ? 'create-github-issue' : 'create-jira-bug'
  const label = target === 'github' ? 'GitHub Issue' : 'Jira Bug'
  const hasToken = target === 'github' ? !!getGithubToken() : !!getJiraToken()

  function getTrackerCredentials() {
    return {
      github_token: target === 'github' ? getGithubToken() : '',
      jira_token: target === 'jira' ? getJiraToken() : '',
      jira_email: target === 'jira' ? getJiraEmail() : '',
      ...(target === 'github' && selectedRepo ? { github_repo_url: selectedRepo } : {}),
      ...(target === 'jira' && jiraProjectKey ? { jira_project_key: jiraProjectKey } : {}),
      ...(target === 'jira' && jiraSecurityLevel ? { jira_security_level: jiraSecurityLevel } : {}),
    }
  }

  // Fetch Jira projects when dialog opens for Jira target
  useEffect(() => {
    if (!open || target !== 'jira') return
    api.get<Array<{key: string; name: string}>>('/api/jira-projects')
      .then((projects) => {
        setJiraProjects(projects)
        if (projects.length > 0) {
          const match = defaultProjectKey
            ? projects.find((p) => p.key === defaultProjectKey)
            : undefined
          setJiraProjectKey(match?.key ?? projects[0].key)
        }
      })
      .catch((err) => console.warn('Failed to fetch Jira projects:', err))
  }, [open, target])

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
        ...getTrackerCredentials(),
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
        ...getTrackerCredentials(),
      })
      setCreatedUrl(res.url)
      setPhase('success')

      // After successful creation, refresh comments to get the server-added comment
      api.get<CommentsAndReviews>(`/results/${jobId}/comments`)
        .then((commentsRes) => dispatch({ type: 'SET_COMMENTS_AND_REVIEWS', payload: commentsRes }))
        .catch(() => {})

      // Also refresh enrichments for the link badges
      refreshEnrichments(jobId)
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
      setSelectedRepo(availableRepos?.[0]?.url ?? '')
      setJiraProjectKey(defaultProjectKey || '')
      setJiraProjects([])
      setJiraSecurityLevel('')
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
            {target === 'github' && availableRepos && availableRepos.length > 1 && (
              <div className="space-y-2">
                <label htmlFor="bug-repo" className="text-xs font-display uppercase tracking-widest text-text-tertiary">Repository</label>
                <select id="bug-repo" value={selectedRepo} onChange={(e) => setSelectedRepo(e.target.value)} className="w-full h-9 rounded-md border border-border-default bg-surface-elevated px-2 text-sm text-text-primary">
                  {availableRepos.map((r) => (
                    <option key={r.url} value={r.url}>{r.name}</option>
                  ))}
                </select>
              </div>
            )}
            {target === 'jira' && jiraProjects.length > 0 && (
              <div className="space-y-2">
                <label htmlFor="bug-jira-project" className="text-xs font-display uppercase tracking-widest text-text-tertiary">Jira Project</label>
                <select id="bug-jira-project" value={jiraProjectKey} onChange={(e) => setJiraProjectKey(e.target.value)} className="w-full h-9 rounded-md border border-border-default bg-surface-elevated px-2 text-sm text-text-primary">
                  {jiraProjects.map((p) => (
                    <option key={p.key} value={p.key}>{p.key} — {p.name}</option>
                  ))}
                </select>
              </div>
            )}
            {target === 'jira' && (
              <div className="space-y-2">
                <label htmlFor="bug-security-level" className="text-xs font-display uppercase tracking-widest text-text-tertiary">Security Level</label>
                <input
                  id="bug-security-level"
                  type="text"
                  value={jiraSecurityLevel}
                  onChange={(e) => setJiraSecurityLevel(e.target.value)}
                  placeholder="e.g. Red Hat Employee"
                  className="w-full h-9 rounded-md border border-border-default bg-surface-elevated px-2 text-sm text-text-primary placeholder:text-text-tertiary"
                />
              </div>
            )}
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
              <label htmlFor="bug-title" className="text-xs font-display uppercase tracking-widest text-text-tertiary">Title</label>
              <Input id="bug-title" value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="space-y-2">
              <label htmlFor="bug-body" className="text-xs font-display uppercase tracking-widest text-text-tertiary">Body</label>
              <Textarea id="bug-body" value={body} onChange={(e) => setBody(e.target.value)} rows={12} className="font-mono text-xs" />
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
            {errorMsg.toLowerCase().includes('token') && errorMsg.toLowerCase().includes('invalid') && (
              <p className="text-xs text-text-tertiary">You can update your tokens in <a href="/settings" className="text-text-link hover:underline">settings</a>.</p>
            )}
          </div>
        )}

        <DialogFooter className="flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
          {phase === 'preview' && (
            <>
              {!hasToken && (
                <p className="text-xs text-text-tertiary">Add a {target === 'github' ? 'GitHub' : 'Jira'} token in <a href="/settings" className="text-text-link hover:underline">settings</a> to create directly.</p>
              )}
              <div className="flex gap-2 sm:ml-auto">
                <Button variant="ghost" onClick={handleClose}>Cancel</Button>
                <Button onClick={handleCreate} disabled={!title.trim() || !hasToken} title={!hasToken ? `Add a ${target === 'github' ? 'GitHub' : 'Jira'} token to create issues` : undefined}>Create {label}</Button>
              </div>
            </>
          )}
          {(phase === 'success' || phase === 'error') && (
            <Button variant="ghost" onClick={handleClose} className="sm:ml-auto">Close</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
