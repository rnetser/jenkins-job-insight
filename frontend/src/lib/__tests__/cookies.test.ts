import { describe, it, expect, beforeEach } from 'vitest'
import { getUsername, setUsername, isLoggedIn, getGithubToken, setGithubToken, getJiraToken, setJiraToken, getJiraEmail, setJiraEmail, clearTokens } from '../cookies'

describe('cookies', () => {
  beforeEach(() => {
    // Clear all cookies
    document.cookie.split(';').forEach((c) => {
      const name = c.split('=')[0].trim()
      if (name) document.cookie = `${name}=; max-age=0; path=/`
    })
    // Clear localStorage
    localStorage.clear()
  })

  it('getUsername returns empty when no cookie set', () => {
    expect(getUsername()).toBe('')
  })

  it('setUsername sets cookie and getUsername reads it', () => {
    setUsername('testuser')
    expect(getUsername()).toBe('testuser')
  })

  it('setUsername trims whitespace', () => {
    setUsername('  padded  ')
    expect(getUsername()).toBe('padded')
  })

  it('isLoggedIn returns false when no cookie', () => {
    expect(isLoggedIn()).toBe(false)
  })

  it('isLoggedIn returns true after setUsername', () => {
    setUsername('user1')
    expect(isLoggedIn()).toBe(true)
  })

  // --- Token helpers ---

  describe.each([
    { label: 'GitHub token', get: getGithubToken, set: setGithubToken, value: 'ghp_abc123' },
    { label: 'Jira token', get: getJiraToken, set: setJiraToken, value: 'jira_token_xyz' },
    { label: 'Jira email', get: getJiraEmail, set: setJiraEmail, value: 'user@example.com' },
  ])('$label helper', ({ get, set, value }) => {
    it('returns empty when unset', () => {
      expect(get()).toBe('')
    })

    it('stores and retrieves value', () => {
      set(value)
      expect(get()).toBe(value)
    })

    it('removes value when set to empty string', () => {
      set(value)
      set('')
      expect(get()).toBe('')
    })

    it('trims whitespace before storing', () => {
      set(`  ${value}  `)
      expect(get()).toBe(value)
    })
  })

  it('clearTokens removes all tokens and email', () => {
    setGithubToken('ghp_abc')
    setJiraToken('jira_xyz')
    setJiraEmail('user@example.com')
    clearTokens()
    expect(getGithubToken()).toBe('')
    expect(getJiraToken()).toBe('')
    expect(getJiraEmail()).toBe('')
  })
})
