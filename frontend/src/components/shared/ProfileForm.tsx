import { useState, type FormEvent, type ReactNode } from 'react'
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
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Eye, EyeOff } from 'lucide-react'

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

function persistTokensToServer(gh: string, je: string, jt: string) {
  api.put('/api/user/tokens', {
    github_token: gh,
    jira_email: je,
    jira_token: jt,
  }).catch((err) => {
    console.error('Failed to sync tokens to server:', err)
  })
}

export function ProfileForm({ onSaved, onAdminLogin }: ProfileFormProps) {
  const [username, setUsernameValue] = useState(getUsername())
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

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = username.trim()
    if (!trimmed) return

    setSaving(true)
    setApiKeyError(null)

    // Try admin login if API key is provided
    if (apiKey.trim() && onAdminLogin) {
      try {
        await onAdminLogin(trimmed, apiKey.trim())
        // Admin login succeeded — also save the username cookie
        setUsername(trimmed)
        setGithubToken(githubToken.trim())
        setJiraEmail(jiraEmail.trim())
        setJiraToken(jiraToken.trim())
        persistTokensToServer(githubToken.trim(), jiraEmail.trim(), jiraToken.trim())
        setSaving(false)
        onSaved()
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
      setSaving(false)

      const results = validations.map((r) => r.status === 'fulfilled' ? r.value : false)
      if (needsGithubValidation && results[0] === false) return
      if (needsJiraValidation && results[1] === false) return
    }

    setUsername(trimmed)
    setGithubToken(githubToken.trim())
    setJiraEmail(jiraEmail.trim())
    setJiraToken(jiraToken.trim())
    persistTokensToServer(githubToken.trim(), jiraEmail.trim(), jiraToken.trim())
    setSaving(false)
    onSaved()
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
              onChange={(e) => setUsernameValue(e.target.value)}
              placeholder="e.g. jdoe"
              autoFocus
              autoComplete="username"
              className="h-10 font-mono"
            />
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
                placeholder="Enter API key..."
                helpContent={<>Admin API key provided by your server administrator.</>}
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

          <Button type="submit" className="w-full" disabled={!username.trim() || saving || validatingGithub || validatingJira}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
          </fieldset>
        </form>
      </CardContent>
    </Card>
  )
}
