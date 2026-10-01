const KEY = 'vigory.auth.token'
export const AUTH_EXPIRED_EVENT = 'vigory:auth-expired'

// sessionStorage (not localStorage): the token dies with the tab.
export const authToken = {
  get: (): string | null => sessionStorage.getItem(KEY),
  set: (token: string) => sessionStorage.setItem(KEY, token),
  clear: () => sessionStorage.removeItem(KEY),
}

export function authHeaders(): Record<string, string> {
  const token = authToken.get()
  return token ? { authorization: `Bearer ${token}` } : {}
}

/** Called on any 401 from a protected route so the app can show the login form. */
export function notifyAuthExpired() {
  authToken.clear()
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}
