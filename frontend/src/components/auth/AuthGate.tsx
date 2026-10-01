import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { ApiError, authApi } from '../../lib/api'
import { AUTH_EXPIRED_EVENT, authToken } from '../../lib/auth'
import { Button } from '../ui/Button'

type State = 'checking' | 'locked' | 'open' | 'error'

/** Renders children only once the backend says auth is off or we hold a
 * token. A 401 anywhere in the app (token expired) re-locks it. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>('checking')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const check = useCallback(() => {
    setState('checking')
    authApi
      .status()
      .then(({ enabled }) => setState(!enabled || authToken.get() ? 'open' : 'locked'))
      .catch(() => {
        setMessage('Cannot reach the backend')
        setState('error')
      })
  }, [])

  useEffect(() => {
    check()
    const lock = () => {
      setMessage('Your session expired. Sign in again.')
      setState('locked')
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, lock)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, lock)
  }, [check])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setMessage('')
    try {
      const { token } = await authApi.login(email.trim(), password)
      authToken.set(token)
      setPassword('')
      setState('open')
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Cannot reach the backend')
    } finally {
      setBusy(false)
    }
  }

  if (state === 'open') return <>{children}</>
  if (state === 'checking')
    return <p className="p-8 text-sm text-[var(--color-text-muted)]">Loading…</p>
  if (state === 'error')
    return (
      <div className="flex flex-col items-start gap-3 p-8">
        <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">{message}</p>
        <Button onClick={check}>Retry</Button>
      </div>
    )

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form
        onSubmit={submit}
        className="flex w-80 flex-col gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-1)] p-5"
      >
        <h1 className="text-sm font-semibold text-[var(--color-text-primary)]">Vigory.ai</h1>
        <p className="text-xs text-[var(--color-text-muted)]">
          Sign in with your voice platform account.
        </p>
        <label htmlFor="access-email" className="text-sm text-[var(--color-text-muted)]">
          Email
        </label>
        <input
          id="access-email"
          type="email"
          autoFocus
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] focus-visible:border-[var(--color-focus)]"
        />
        <label htmlFor="access-password" className="text-sm text-[var(--color-text-muted)]">
          Password
        </label>
        <input
          id="access-password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-0)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] focus-visible:border-[var(--color-focus)]"
        />
        {message && (
          <p role="alert" className="text-sm text-[var(--color-status-destroyed)]">{message}</p>
        )}
        <Button variant="primary" type="submit" disabled={!email.trim() || !password || busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </div>
  )
}
