import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api, ApiError, userErrorMessage } from '../api'

describe('api', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('returns parsed JSON on success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const result = await api.get<{ status: string }>('/test')
    expect(result.status).toBe('ok')
  })

  it('throws ApiError on non-ok response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'not found' }), {
        status: 404,
        statusText: 'Not Found',
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await expect(api.get('/missing')).rejects.toThrow(ApiError)
  })

  it('returns undefined for 204 No Content', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 204 }),
    )
    const result = await api.delete('/item')
    expect(result).toBeUndefined()
  })

  it('post sends JSON body', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 1 }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await api.post('/create', { name: 'test' })
    const [, options] = fetchSpy.mock.calls[0]
    expect(options?.method).toBe('POST')
    expect(options?.body).toBe(JSON.stringify({ name: 'test' }))
  })
})

describe('userErrorMessage', () => {
  it('extracts string detail from ApiError body', () => {
    const err = new ApiError(404, 'Not Found', { detail: 'Job not found' })
    expect(userErrorMessage(err)).toBe('Job not found')
  })

  it('summarises array detail from ApiError body (FastAPI validation)', () => {
    const err = new ApiError(422, 'Unprocessable Entity', {
      detail: [
        { loc: ['body', 'name'], msg: 'field required', type: 'value_error.missing' },
        { loc: ['body', 'age'], msg: 'not a valid integer', type: 'type_error.integer' },
      ],
    })
    expect(userErrorMessage(err)).toBe('field required (and 1 more)')
  })

  it('shows count for array detail without msg field', () => {
    const err = new ApiError(422, 'Unprocessable Entity', {
      detail: ['error1', 'error2'],
    })
    expect(userErrorMessage(err)).toBe('Validation failed (2 errors)')
  })

  it('falls back to ApiError message when no detail', () => {
    const err = new ApiError(500, 'Internal Server Error', { error: 'something' })
    expect(userErrorMessage(err)).toBe('API error 500: Internal Server Error')
  })

  it('uses Error.message for non-ApiError errors', () => {
    const err = new TypeError('network failure')
    expect(userErrorMessage(err)).toBe('network failure')
  })

  it('returns fallback for non-Error values', () => {
    expect(userErrorMessage('boom')).toBe('An unexpected error occurred')
    expect(userErrorMessage(null, 'custom fallback')).toBe('custom fallback')
  })

  it('handles single-item array detail without extra count', () => {
    const err = new ApiError(422, 'Unprocessable Entity', {
      detail: [{ msg: 'field required' }],
    })
    expect(userErrorMessage(err)).toBe('field required')
  })
})
