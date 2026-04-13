import { useState, useCallback } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { Upload, Loader2, CheckCircle2, AlertTriangle, XCircle } from 'lucide-react'
import type { ReportPortalPushResult } from '@/types'

interface ReportPortalButtonProps {
  jobId: string
}

export function ReportPortalButton({ jobId }: ReportPortalButtonProps) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [pushing, setPushing] = useState(false)
  const [resultDialogOpen, setResultDialogOpen] = useState(false)
  const [pushResult, setPushResult] = useState<ReportPortalPushResult | null>(null)
  const [pushError, setPushError] = useState('')

  const handlePush = useCallback(async () => {
    setConfirmOpen(false)
    setPushing(true)
    setPushError('')
    setPushResult(null)
    try {
      const result = await api.post<ReportPortalPushResult>(`/results/${jobId}/push-reportportal`)
      setPushResult(result)
      setResultDialogOpen(true)
    } catch (err) {
      setPushError(err instanceof Error ? err.message : 'Failed to push to Report Portal')
      setResultDialogOpen(true)
    } finally {
      setPushing(false)
    }
  }, [jobId])

  const handleClose = useCallback(() => {
    setResultDialogOpen(false)
  }, [])

  const hasResultErrors = !!(pushResult && pushResult.errors.length > 0)
  const hasUnmatched = !!(pushResult && pushResult.unmatched.length > 0)
  const isFullFailure = pushError || (pushResult && pushResult.pushed === 0 && hasResultErrors)
  const isPartialSuccess = pushResult && pushResult.pushed > 0 && (hasResultErrors || hasUnmatched)
  const isNoop = !!(pushResult && pushResult.pushed === 0 && !hasResultErrors && hasUnmatched)

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="gap-1.5 text-xs"
        onClick={() => setConfirmOpen(true)}
        disabled={pushing}
      >
        {pushing ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Upload className="h-3.5 w-3.5" />
        )}
        {pushing ? 'Pushing...' : 'Push to Report Portal'}
      </Button>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-[400px] bg-surface-card border-border-default">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Upload className="h-5 w-5 text-text-secondary" />
              Confirm Push
            </DialogTitle>
            <DialogDescription>
              Push failure classifications to Report Portal?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handlePush}>
              Push
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={resultDialogOpen} onOpenChange={setResultDialogOpen}>
        <DialogContent className="sm:max-w-[520px] bg-surface-card border-border-default overflow-hidden">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {isFullFailure ? (
                <><XCircle className="h-5 w-5 text-signal-red" /> Failed to push classifications to Report Portal.</>
              ) : isPartialSuccess || isNoop ? (
                <><AlertTriangle className="h-5 w-5 text-signal-orange" /> {isNoop ? 'No classifications could be matched.' : 'Some classifications could not be pushed.'}</>
              ) : (
                <><CheckCircle2 className="h-5 w-5 text-signal-green" /> Pushed {pushResult?.pushed ?? 0} classification{pushResult?.pushed !== 1 ? 's' : ''} to Report Portal.</>
              )}
            </DialogTitle>
            {pushResult?.launch_id != null && (
              <p className="text-xs text-text-tertiary mt-1">
                Launch ID: <span className="font-mono">{pushResult.launch_id}</span>
              </p>
            )}
          </DialogHeader>

          <div className="space-y-3 py-2 min-w-0">
            {pushError && (
              <p className="text-sm text-signal-red break-words">{pushError}</p>
            )}

            {pushResult && pushResult.unmatched.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1.5 text-sm text-signal-orange">
                  <AlertTriangle className="h-4 w-4 flex-shrink-0" />
                  <span>{pushResult.unmatched.length} unmatched test{pushResult.unmatched.length !== 1 ? 's' : ''}</span>
                </div>
                <ul className="ml-6 space-y-0.5 text-xs text-text-tertiary max-h-32 overflow-y-auto">
                  {pushResult.unmatched.map((name, index) => (
                    <li key={`${name}-${index}`} className="font-mono break-all" title={name}>{name}</li>
                  ))}
                </ul>
              </div>
            )}

            {pushResult && pushResult.errors.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1.5 text-sm text-signal-red">
                  <XCircle className="h-4 w-4 flex-shrink-0" />
                  <span>{pushResult.errors.length} error{pushResult.errors.length !== 1 ? 's' : ''}</span>
                </div>
                <ul className="ml-6 space-y-1 text-xs text-signal-red/80 max-h-48 overflow-y-auto">
                  {pushResult.errors.map((err, i) => (
                    <li key={i} className="break-words" title={err}>{err}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={handleClose}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
