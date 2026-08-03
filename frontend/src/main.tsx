import { render } from 'preact'
import { App } from './app'
import { api } from './api'
import './styles.css'

// PAT bootstrap (SPEC22 §3). An agent seeds a Personal Access Token into this
// origin's sessionStorage BEFORE navigation; we consume-and-delete it here, on
// boot, and exchange it for the session cookie. The token never appears in the
// URL, history, Referer, or any DOM element.
//
// Genuine single-use: the init script owns the `easycad_pat_used` marker (set at
// seed time, before the token, regardless of exchange outcome), so a reload never
// re-seeds — we only read+delete+exchange here. Any failure (401 / network / 5xx
// / timeout) still renders the SPA on the free trial with a neutral auth-error.
async function boot() {
  let authError: string | null = null
  const t = sessionStorage.getItem('easycad_pat')
  if (t) {
    sessionStorage.removeItem('easycad_pat') // clear BEFORE the call — never retry the secret
    try {
      await api.authWithToken(t, { timeoutMs: 5000 }) // bounds a hung request so render always runs
    } catch {
      authError = 'invalid-token' // 401 / network / 5xx / malformed / timeout — all here
      // Best-effort cleanup only; NOT awaited, so a hung logout can't re-block render.
      // The real isolation guarantee is the agent's fresh BrowserContext (§3.0).
      api.logout({ timeoutMs: 5000 }).catch(() => {})
    }
  }
  render(<App authError={authError} />, document.getElementById('app')!)
}

boot()
