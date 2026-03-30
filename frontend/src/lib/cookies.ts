const COOKIE_NAME = 'jji_username'

export function getUsername(): string {
  const match = document.cookie.match(
    new RegExp(`(?:^|;\\s*)${COOKIE_NAME}=([^;]*)`)
  )
  return match ? decodeURIComponent(match[1]) : ''
}

export function setUsername(username: string): void {
  const encoded = encodeURIComponent(username.trim())
  // 1 year expiry, path=/ so all routes can read it
  document.cookie = `${COOKIE_NAME}=${encoded}; path=/; max-age=${365 * 24 * 60 * 60}; SameSite=Lax`
}

export function clearUsername(): void {
  document.cookie = `${COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax`
}

export function isLoggedIn(): boolean {
  return getUsername() !== ''
}
