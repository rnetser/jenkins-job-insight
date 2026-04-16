/** Centralized fetch wrapper for the JJI API. */

class ApiError extends Error {
  status: number
  statusText: string
  body: unknown

  constructor(status: number, statusText: string, body: unknown) {
    super(`API error ${status}: ${statusText}`)
    this.name = 'ApiError'
    this.status = status
    this.statusText = statusText
    this.body = body
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!res.ok) {
    let body: unknown
    try {
      const text = await res.text()
      try {
        body = JSON.parse(text)
      } catch {
        body = text
      }
    } catch {
      body = null
    }
    throw new ApiError(res.status, res.statusText, body)
  }

  // 204 No Content — intentional cast; callers (e.g. api.delete) do not use the return value.
  if (res.status === 204) return undefined as T

  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),

  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  delete: <T>(path: string) =>
    request<T>(path, { method: 'DELETE' }),
}

/** Extract a short user-facing message from an error.
 *
 *  - ApiError with a string `detail` → use it directly.
 *  - ApiError with an array `detail` (FastAPI validation) → short summary.
 *  - ApiError without detail → "API error {status}: {statusText}".
 *  - Any other Error → its `.message`.
 *  - Non-Error → generic fallback.
 */
export function userErrorMessage(err: unknown, fallback = 'An unexpected error occurred'): string {
  if (err instanceof ApiError) {
    const body = err.body as Record<string, unknown> | null | undefined
    if (body && typeof body === 'object') {
      if (typeof body.detail === 'string') return body.detail
      if (Array.isArray(body.detail) && body.detail.length > 0) {
        const first = body.detail[0]
        const msg = typeof first === 'object' && first !== null && 'msg' in first
          ? String((first as Record<string, unknown>).msg)
          : undefined
        const count = body.detail.length
        return msg
          ? `${msg}${count > 1 ? ` (and ${count - 1} more)` : ''}`
          : `Validation failed (${count} error${count !== 1 ? 's' : ''})`
      }
    }
    return err.message
  }
  if (err instanceof Error) return err.message
  return fallback
}

export { ApiError }
