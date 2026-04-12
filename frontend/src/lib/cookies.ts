const COOKIE_NAME = 'jji_username'
const GITHUB_TOKEN_KEY = 'jji_github_token'
const JIRA_TOKEN_KEY = 'jji_jira_token'
const JIRA_EMAIL_KEY = 'jji_jira_email'

export function getUsername(): string {
  const match = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${COOKIE_NAME}=([^;]*)`)
  )
  return match ? decodeURIComponent(match[1]) : ''
}

export function setUsername(username: string): void {
  const encoded = encodeURIComponent(username.trim())
  document.cookie = `${COOKIE_NAME}=${encoded}; path=/; max-age=${365 * 24 * 60 * 60}; SameSite=Lax`
}

export function clearUsername(): void {
  document.cookie = `${COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax`
}

export function isLoggedIn(): boolean {
  return getUsername() !== ''
}

function readStoredValue(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function writeStoredValue(key: string, value: string): void {
  try {
    const trimmed = value.trim()
    if (trimmed) {
      localStorage.setItem(key, trimmed)
    } else {
      localStorage.removeItem(key)
    }
  } catch {
    // Storage unavailable — silently ignore
  }
}

export function getGithubToken(): string {
  return readStoredValue(GITHUB_TOKEN_KEY)
}

export function setGithubToken(token: string): void {
  writeStoredValue(GITHUB_TOKEN_KEY, token)
}

export function getJiraToken(): string {
  return readStoredValue(JIRA_TOKEN_KEY)
}

export function setJiraToken(token: string): void {
  writeStoredValue(JIRA_TOKEN_KEY, token)
}

export function getJiraEmail(): string {
  return readStoredValue(JIRA_EMAIL_KEY)
}

export function setJiraEmail(email: string): void {
  writeStoredValue(JIRA_EMAIL_KEY, email)
}

export function clearTokens(): void {
  try {
    localStorage.removeItem(GITHUB_TOKEN_KEY)
    localStorage.removeItem(JIRA_TOKEN_KEY)
    localStorage.removeItem(JIRA_EMAIL_KEY)
  } catch {
    // Storage unavailable — silently ignore
  }
}
