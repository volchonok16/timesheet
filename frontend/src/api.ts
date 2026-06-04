const SESSION_KEY = 'tfsTimesheetSessionId'

function resolveApiBase(): string {
  const fromEnv = (import.meta.env.VITE_API_URL as string | undefined)?.trim().replace(/\/$/, '') ?? ''
  if (fromEnv) return fromEnv
  if (typeof window === 'undefined') return ''
  const { hostname, protocol, port } = window.location
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    if (port === '15173') return `${protocol}//${hostname}:18080`
    // За nginx (18080, 8080, 80 и т.д.) — относительные пути /api
    return ''
  }
  // Production: отдельный API-домен задаётся через VITE_API_URL
  return ''
}

const apiBase = resolveApiBase()

export function getSessionId(): string | null {
  return sessionStorage.getItem(SESSION_KEY)
}

export function setSessionId(sessionId: string) {
  sessionStorage.setItem(SESSION_KEY, sessionId)
}

export function clearSessionId() {
  sessionStorage.removeItem(SESSION_KEY)
}

export function applySessionFromUrl() {
  const params = new URLSearchParams(window.location.search)
  const sessionId = params.get('session')
  if (!sessionId) return false
  setSessionId(sessionId)
  params.delete('session')
  const next = `${window.location.pathname}${params.toString() ? `?${params}` : ''}`
  window.history.replaceState({}, '', next)
  return true
}

export async function readApiError(response: Response): Promise<string> {
  const text = await response.text()
  try {
    const data = JSON.parse(text) as { detail?: unknown }
    if (typeof data.detail === 'string') return data.detail
  } catch {
    /* not json */
  }
  return text || response.statusText
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const sessionId = getSessionId()
  if (sessionId) headers.set('X-Session-Id', sessionId)
  return fetch(`${apiBase}${path}`, { ...init, headers })
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await apiFetch(path)
  if (!response.ok) throw new Error(await readApiError(response))
  return response.json()
}

export { apiBase }
