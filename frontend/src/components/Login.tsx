import { useEffect, useState, type FormEvent } from 'react'
import { apiFetch, getJson, readApiError, setSessionId } from '../api'

type AuthDefaults = {
  baseUrl: string
  project: string
  projectId?: string | null
  version: string
}

type LoginProps = {
  onSuccess: () => void
}

type AuthMode = 'account' | 'token'

export default function Login({ onSuccess }: LoginProps) {
  const [mode, setMode] = useState<AuthMode>('account')
  const [baseUrl, setBaseUrl] = useState('https://tfs.t2.ru/tfs/Main')
  const [project, setProject] = useState('Tele2')
  const [projectId, setProjectId] = useState('')
  const [domain, setDomain] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [pat, setPat] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void getJson<AuthDefaults>('/api/auth/defaults')
      .then((payload) => {
        setBaseUrl(payload.baseUrl)
        setProject(payload.project)
        setProjectId(payload.projectId ?? '')
      })
      .catch(() => undefined)
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setLoading(true)
    setError(null)

    const body = {
      baseUrl: baseUrl.trim(),
      project: project.trim(),
      projectId: projectId.trim() || null,
      domain: domain.trim() || null,
      username: mode === 'account' ? username.trim() : null,
      password: mode === 'account' ? password : null,
      pat: mode === 'token' ? pat.trim() : null,
      cookie: null,
      extraHeaders: null,
    }

    if (mode === 'account' && (!body.username || !body.password)) {
      setError('Введите учётную запись и пароль.')
      setLoading(false)
      return
    }
    if (mode === 'token' && !body.pat) {
      setError('Введите токен PAT.')
      setLoading(false)
      return
    }

    try {
      const response = await apiFetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!response.ok) throw new Error(await readApiError(response))
      const payload = (await response.json()) as { sessionId: string }
      setSessionId(payload.sessionId)
      onSuccess()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось войти в TFS')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-card">
        <header className="login-hero">
          <div className="login-brand">
            <span className="brand-mark">TS</span>
            <div>
              <p className="eyebrow">TFS Timesheet</p>
              <h1>Вход в TFS</h1>
            </div>
          </div>
          <p className="login-lead">
            Авторизация как в Ganta/Roadmap: логин и пароль или PAT. Для <strong>@t2.ru</strong> обычно
            нужен PAT.
          </p>
        </header>

        <div className="login-mode-switch" role="tablist">
          <button type="button" className={mode === 'account' ? 'active' : ''} onClick={() => setMode('account')}>
            Учётная запись
          </button>
          <button type="button" className={mode === 'token' ? 'active' : ''} onClick={() => setMode('token')}>
            Токен PAT
          </button>
        </div>

        <form className="stack login-form" onSubmit={submit}>
          {mode === 'account' ? (
            <>
              <label className="field">
                <span>Логин</span>
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="TELE2\\ivanov"
                  autoComplete="username"
                  required
                />
              </label>
              <label className="field">
                <span>Пароль</span>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  required
                />
              </label>
            </>
          ) : (
            <label className="field">
              <span>Personal Access Token</span>
              <input
                type="password"
                value={pat}
                onChange={(e) => setPat(e.target.value)}
                placeholder="TFS → User settings → PAT"
                autoComplete="off"
                required
              />
            </label>
          )}

          {error && <p className="error-banner">{error}</p>}

          <button type="submit" className="btn primary wide" disabled={loading}>
            {loading ? 'Проверяем…' : 'Войти'}
          </button>
        </form>

        <button type="button" className="link-btn" onClick={() => setShowAdvanced((v) => !v)}>
          {showAdvanced ? 'Скрыть настройки' : 'Настройки TFS'}
        </button>

        {showAdvanced && (
          <div className="stack compact">
            <label className="field">
              <span>Домен AD</span>
              <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="TELE2" />
            </label>
            <label className="field">
              <span>TFS URL</span>
              <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            </label>
            <div className="field-row">
              <label className="field">
                <span>Проект</span>
                <input value={project} onChange={(e) => setProject(e.target.value)} />
              </label>
              <label className="field">
                <span>Project ID</span>
                <input value={projectId} onChange={(e) => setProjectId(e.target.value)} />
              </label>
            </div>
          </div>
        )}
      </section>
    </main>
  )
}
