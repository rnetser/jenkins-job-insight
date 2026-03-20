import { describe, it, expect, beforeEach } from 'vitest'
import { getUsername, setUsername, isLoggedIn } from '../cookies'

describe('cookies', () => {
  beforeEach(() => {
    // Clear all cookies
    document.cookie.split(';').forEach((c) => {
      const name = c.split('=')[0].trim()
      if (name) document.cookie = `${name}=; max-age=0; path=/`
    })
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
})
