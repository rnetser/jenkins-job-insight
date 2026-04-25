import { useState, useEffect, useCallback, type FormEvent, type ReactNode } from 'react'
import { api, ApiError } from '@/lib/api'
import {
  setUsername,
  setGithubToken,
  setJiraToken,
  setJiraEmail,
  getUsername,
  getGithubToken,
  getJiraToken,
  getJiraEmail,
} from '@/lib/cookies'
import {
  getPushSubscriptionState,
  hasActivePushSubscription,
  subscribeToPush,
  unsubscribeFromPush,
} from '@/lib/notifications'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Eye, EyeOff, Bell, BellOff, ShieldCheck } from 'lucide-react'
import { useAuth } from '@/lib/auth'

interface ProfileFormProps {
  onSaved: () => void | Promise<void>
  onAdminLogin?: (username: string, apiKey: string) => Promise<void>
}

interface TokenValidationResult {
  valid: boolean
  username: string
  message: string
}

function TokenField({ id, label, value, onChange, show, onToggleShow, validation, error, placeholder, helpContent, optionalLabel = true }: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  show: boolean
  onToggleShow: () => void
  validation: TokenValidationResult | null
  error?: string | null
  placeholder: string
  helpContent: ReactNode
  optionalLabel?: boolean
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block font-display text-xs font-medium uppercase tracking-widest text-text-secondary">
        {label} {optionalLabel && <span className="text-text-tertiary font-normal normal-case tracking-normal">(optional)</span>}
      </label>
      <div className="relative">
        <Input id={id} type={show ? 'text' : 'password'} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} autoComplete="off" className="h-10 pr-10 font-mono" />
        <button type="button" onClick={onToggleShow} className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-text-tertiary hover:text-text-secondary transition-colors" aria-label={show ? 'Hide token' : 'Show token'}>
          {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {validation && (
        <p className={`text-xs ${validation.valid ? 'text-signal-green' : 'text-signal-red'}`}>{validation.message}</p>
      )}
      {error && (
        <p className="text-xs text-signal-red">{error}</p>
      )}
      <p className="text-xs text-text-tertiary">{helpContent}</p>
    </div>
  )
}

async function persistTokensToServer(gh: string, je: string, jt: string) {
  // Don't overwrite server tokens with empty values
  if (!gh && !je && !jt) return
  try {
    await api.put('/api/user/tokens', {
      github_token: gh,
      jira_email: je,
      jira_token: jt,
    })
  } catch (err) {
    // May fail with 404 on first registration (user not yet in DB)
    // or 401 if cookie not set yet — both are expected, not errors
    if (!(err instanceof ApiError && (err.status === 404 || err.status === 401))) {
      console.error('Failed to sync tokens to server:', err)
    }
  }
}

type PushState = 'granted' | 'denied' | 'default' | 'unsupported'

function NotificationToggle() {
  const [pushState, setPushState] = useState<PushState>('default')
  const [hasSubscription, setHasSubscription] = useState(false)
  const [toggling, setToggling] = useState(false)
  const [toggleError, setToggleError] = useState<string | null>(null)

  const refreshState = useCallback(async () => {
    const state = await getPushSubscriptionState()
    setPushState(state)
    setHasSubscription(await hasActivePushSubscription())
  }, [])

  useEffect(() => { refreshState() }, [refreshState])

  async function handleToggle() {
    setToggling(true)
    setToggleError(null)
    try {
      if (hasSubscription) {
        const ok = await unsubscribeFromPush()
        if (ok) setHasSubscription(false)
        else setToggleError('Failed to disable notifications')
      } else {
        const result = await subscribeToPush()
        if (result.ok) {
          setHasSubscription(true)
        } else {
          setToggleError(result.error || 'Failed to enable notifications')
        }
      }
      await refreshState()
    } catch (err) {
      setToggleError(err instanceof Error ? err.message : 'Unexpected error')
    } finally {
      setToggling(false)
    }
  }

  if (pushState === 'unsupported') {
    return (
      <div className="space-y-1.5 pt-2">
        <div className="flex items-center gap-3 py-1">
          <div className="h-px flex-1 bg-border-muted" />
          <span className="font-display text-[10px] uppercase tracking-widest text-text-tertiary">Push Notifications</span>
          <div className="h-px flex-1 bg-border-muted" />
        </div>
        <p className="text-xs text-text-tertiary">Push notifications are not supported in this browser.</p>
      </div>
    )
  }

  if (pushState === 'denied') {
    return (
      <div className="space-y-1.5 pt-2">
        <div className="flex items-center gap-3 py-1">
          <div className="h-px flex-1 bg-border-muted" />
          <span className="font-display text-[10px] uppercase tracking-widest text-text-tertiary">Push Notifications</span>
          <div className="h-px flex-1 bg-border-muted" />
        </div>
        <div className="flex items-center gap-2 text-xs text-signal-amber">
          <BellOff className="h-4 w-4 shrink-0" />
          <span>Notifications blocked. To re-enable, update this site&apos;s notification permission in your browser settings.</span>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-1.5 pt-2">
      <div className="flex items-center gap-3 py-1">
        <div className="h-px flex-1 bg-border-muted" />
        <span className="font-display text-[10px] uppercase tracking-widest text-text-tertiary">Push Notifications</span>
        <div className="h-px flex-1 bg-border-muted" />
      </div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          {hasSubscription ? <Bell className="h-4 w-4 text-signal-green" /> : <BellOff className="h-4 w-4" />}
          <span>{hasSubscription ? 'Notifications enabled' : 'Notifications disabled'}</span>
        </div>
        <Button type="button" variant={hasSubscription ? 'outline' : 'default'} size="sm" disabled={toggling} onClick={handleToggle}>
          {toggling ? 'Updating...' : hasSubscription ? 'Disable' : 'Enable'}
        </Button>
      </div>
      {toggleError && (
        <p className="text-xs text-signal-red">{toggleError}</p>
      )}
      <p className="text-xs text-text-tertiary">Receive browser notifications when someone mentions you in a comment.</p>
    </div>
  )
}

export function ProfileForm({ onSaved, onAdminLogin }: ProfileFormProps) {
  const { isAdmin } = useAuth()
  const [initialUsername] = useState(getUsername)
  const [username, setUsernameValue] = useState(initialUsername)
  const [apiKey, setApiKey] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)
  const [apiKeyError, setApiKeyError] = useState<string | null>(null)
  const [githubToken, setGithubTokenValue] = useState(getGithubToken())
  const [jiraEmail, setJiraEmailValue] = useState(getJiraEmail())
  const [jiraToken, setJiraTokenValue] = useState(getJiraToken())
  const [showGithubToken, setShowGithubToken] = useState(false)
  const [showJiraToken, setShowJiraToken] = useState(false)
  const [validatingGithub, setValidatingGithub] = useState(false)
  const [validatingJira, setValidatingJira] = useState(false)
  const [githubValidation, setGithubValidation] = useState<TokenValidationResult | null>(null)
  const [jiraValidation, setJiraValidation] = useState<TokenValidationResult | null>(null)

  const [saving, setSaving] = useState(false)
  const [usernameError, setUsernameError] = useState<string | null>(null)
  const [tokensLoaded, setTokensLoaded] = useState(false)

  useEffect(() => {
    if (!initialUsername) {
      setTokensLoaded(true) // no user yet, nothing to load
      return
    }
    async function loadTokens() {
      try {
        const tokens = await api.get<{ github_token: string; jira_email: string; jira_token: string }>('/api/user/tokens')
        if (tokens.github_token) {
          setGithubTokenValue(tokens.github_token)
          setGithubToken(tokens.github_token)
        }
        if (tokens.jira_email) {
          setJiraEmailValue(tokens.jira_email)
          setJiraEmail(tokens.jira_email)
        }
        if (tokens.jira_token) {
          setJiraTokenValue(tokens.jira_token)
          setJiraToken(tokens.jira_token)
        }
      } catch {
        // Server tokens not available — keep localStorage values
      } finally {
        setTokensLoaded(true)
      }
    }
    loadTokens()
  // eslint-disable-next-line react-hooks/exhaustive-deps -- initialUsername and cookie setters are stable refs
  }, [])

  async function refreshTokensFromServer() {
    try {
      const freshTokens = await api.get<{ github_token: string; jira_email: string; jira_token: string }>('/api/user/tokens')
      if (freshTokens.github_token && !githubToken.trim()) {
        setGithubTokenValue(freshTokens.github_token)
        setGithubToken(freshTokens.github_token)
      }
      if (freshTokens.jira_email && !jiraEmail.trim()) {
        setJiraEmailValue(freshTokens.jira_email)
        setJiraEmail(freshTokens.jira_email)
      }
      if (freshTokens.jira_token && !jiraToken.trim()) {
        setJiraTokenValue(freshTokens.jira_token)
        setJiraToken(freshTokens.jira_token)
      }
    } catch {
      // ignore — tokens may not be available yet
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = username.trim()
    if (!trimmed) return

    if (trimmed.toLowerCase() === 'admin') {
      setUsernameError("The username 'admin' is reserved")
      return
    }
    setUsernameError(null)

    setSaving(true)
    setApiKeyError(null)

    async function commitProfile(trimmedUsername: string) {
      setUsername(trimmedUsername)
      // Only persist tokens if user actually entered values
      const gh = githubToken.trim()
      const je = jiraEmail.trim()
      const jt = jiraToken.trim()
      if (gh || je || jt) {
        setGithubToken(gh)
        setJiraEmail(je)
        setJiraToken(jt)
        await persistTokensToServer(gh, je, jt)
      }
    }

    // Try admin login if API key is provided
    if (apiKey.trim() && onAdminLogin) {
      try {
        await onAdminLogin(trimmed, apiKey.trim())
        // Admin login succeeded — also save the username cookie
        await commitProfile(trimmed)
        setSaving(false)
        // Re-fetch tokens from server before navigating away (onSaved unmounts the component)
        await refreshTokensFromServer()
        await onSaved()
        return
      } catch (err) {
        setSaving(false)
        if (err instanceof ApiError && err.status === 401) {
          setApiKeyError('Invalid username or API key')
        } else {
          setApiKeyError('Login failed — please try again')
        }
        return
      }
    }

    const needsGithubValidation = githubToken.trim() && (!githubValidation || !githubValidation.valid)
    const needsJiraValidation = jiraToken.trim() && (!jiraValidation || !jiraValidation.valid)

    if (needsGithubValidation || needsJiraValidation) {
      const validations = await Promise.allSettled([
        needsGithubValidation ? validateGithub() : Promise.resolve(),
        needsJiraValidation ? validateJira() : Promise.resolve(),
      ])

      const results = validations.map((r) => r.status === 'fulfilled' ? r.value : false)
      if (needsGithubValidation && results[0] === false) { setSaving(false); return }
      if (needsJiraValidation && results[1] === false) { setSaving(false); return }
    }

    await commitProfile(trimmed)
    setSaving(false)
    // Re-fetch tokens from server before navigating away (onSaved unmounts the component)
    await refreshTokensFromServer()
    await onSaved()
  }

  async function validateToken(
    tokenType: 'github' | 'jira',
    payload: Record<string, string>,
    setValidating: (v: boolean) => void,
    setValidation: (r: TokenValidationResult | null) => void,
  ): Promise<boolean> {
    setValidating(true)
    setValidation(null)
    try {
      const result = await api.post<TokenValidationResult>('/api/validate-token', {
        token_type: tokenType,
        ...payload,
      })
      setValidation(result)
      return result.valid
    } catch {
      setValidation({ valid: false, username: '', message: 'Validation request failed' })
      return false
    } finally {
      setValidating(false)
    }
  }

  function validateGithub(): Promise<boolean> {
    return validateToken('github', { token: githubToken.trim() }, setValidatingGithub, setGithubValidation)
  }

  function validateJira(): Promise<boolean> {
    const email = jiraEmail.trim()
    return validateToken('jira', email ? { token: jiraToken.trim(), email } : { token: jiraToken.trim() }, setValidatingJira, setJiraValidation)
  }

  return (
    <Card className="border-border-muted">
      <CardContent className="p-5">
        <form onSubmit={handleSubmit} className="space-y-4">
          <fieldset disabled={saving || validatingGithub || validatingJira} className="space-y-4">
          {/* Username field */}
          <div className="space-y-1.5">
            <label
              htmlFor="username"
              className="block font-display text-xs font-medium uppercase tracking-widest text-text-secondary"
            >
              Username
            </label>
            <Input
              id="username"
              value={username}
              onChange={(e) => { setUsernameValue(e.target.value); setUsernameError(null) }}
              placeholder="e.g. jdoe"
              autoFocus
              autoComplete="username"
              className="h-10 font-mono"
            />
            {usernameError && (
              <p className="text-xs text-signal-red">{usernameError}</p>
            )}
          </div>

          {onAdminLogin && (
            <>
              {/* Admin Authentication Divider */}
              <div className="flex items-center gap-3 py-1">
                <div className="h-px flex-1 bg-border-muted" />
                <span className="font-display text-[10px] uppercase tracking-widest text-text-tertiary">
                  Admin Authentication
                </span>
                <div className="h-px flex-1 bg-border-muted" />
              </div>
              <p className="text-xs text-text-tertiary">
                Provide your API key for admin access. Leave empty for regular user access.
              </p>

              {/* API Key field */}
              <TokenField
                id="api-key"
                label="API Key"
                value={apiKey}
                onChange={(v) => { setApiKey(v); setApiKeyError(null) }}
                show={showApiKey}
                onToggleShow={() => setShowApiKey(!showApiKey)}
                validation={null}
                error={apiKeyError}
                placeholder={isAdmin ? 'Authenticated ✓' : 'Enter API key...'}
                helpContent={
                  isAdmin && !apiKey.trim()
                    ? <span className="inline-flex items-center gap-1 text-signal-green"><ShieldCheck className="h-3 w-3" />Authenticated as admin</span>
                    : <>Admin API key provided by your server administrator.</>
                }
              />
            </>
          )}

          {/* Tracker Tokens Divider */}
          <div className="flex items-center gap-3 py-1">
            <div className="h-px flex-1 bg-border-muted" />
            <span className="font-display text-[10px] uppercase tracking-widest text-text-tertiary">
              Tracker Tokens
            </span>
            <div className="h-px flex-1 bg-border-muted" />
          </div>
          <p className="text-xs text-text-tertiary">
            Provide your personal tokens to create issues and bugs directly
            under your name. Without tokens, you can still preview generated
            content but cannot submit.
          </p>

          {/* GitHub Token field */}
          <TokenField
            id="github-token"
            label="GitHub Token"
            value={githubToken}
            onChange={(v) => { setGithubTokenValue(v); setGithubValidation(null) }}
            show={showGithubToken}
            onToggleShow={() => setShowGithubToken(!showGithubToken)}
            validation={githubValidation}
            placeholder="ghp_..."
            helpContent={<>Personal Access Token with{' '}<code className="text-text-secondary">repo</code> scope.{' '}<a href="https://github.com/settings/tokens" target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline">Generate token →</a></>}
          />

          {/* Jira Email + Token fields */}
          <div className="space-y-1.5">
            <label
              htmlFor="jira-email"
              className="block font-display text-xs font-medium uppercase tracking-widest text-text-secondary"
            >
              Jira Email{' '}
              <span className="text-text-tertiary font-normal normal-case tracking-normal">
                (optional)
              </span>
            </label>
            <Input
              id="jira-email"
              type="email"
              value={jiraEmail}
              onChange={(e) => {
                setJiraEmailValue(e.target.value)
                setJiraValidation(null)
              }}
              placeholder="e.g. jdoe@company.com"
              autoComplete="email"
              className="h-10 font-mono"
            />
            <p className="text-xs text-text-tertiary">
              Required for Jira Cloud authentication. Use the same email as
              your Atlassian account.
            </p>
          </div>

          <TokenField
            id="jira-token"
            label="Jira Token"
            value={jiraToken}
            onChange={(v) => { setJiraTokenValue(v); setJiraValidation(null) }}
            show={showJiraToken}
            onToggleShow={() => setShowJiraToken(!showJiraToken)}
            validation={jiraValidation}
            placeholder="Token..."
            helpContent={<>Jira Cloud: API token from{' '}<a href="https://id.atlassian.com/manage-profile/security/api-tokens" target="_blank" rel="noopener noreferrer" className="text-text-link hover:underline">Atlassian account →</a>{' '}· Jira Server/DC: Personal Access Token</>}
          />

          <Button type="submit" className="w-full" disabled={!username.trim() || saving || validatingGithub || validatingJira || !tokensLoaded}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
          </fieldset>

          {/* Push Notifications */}
          {initialUsername && <NotificationToggle />}

        </form>
      </CardContent>
    </Card>
  )
}
