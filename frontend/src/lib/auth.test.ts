import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AUTH_EXPIRED_EVENT, authHeaders, authToken, notifyAuthExpired } from './auth'

describe('auth helpers', () => {
  beforeEach(() => sessionStorage.clear())

  it('sends no header without a token, a bearer header with one', () => {
    expect(authHeaders()).toEqual({})
    authToken.set('abc')
    expect(authHeaders()).toEqual({ authorization: 'Bearer abc' })
  })

  it('expiry clears the token and notifies listeners', () => {
    authToken.set('abc')
    const spy = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, spy)
    notifyAuthExpired()
    window.removeEventListener(AUTH_EXPIRED_EVENT, spy)
    expect(authToken.get()).toBeNull()
    expect(spy).toHaveBeenCalledOnce()
  })
})
