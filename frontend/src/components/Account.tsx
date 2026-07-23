import { useState } from 'preact/hooks'
import { useStore } from '../store'
import { IconUser } from './Icons'

export function Account() {
  const authenticated = useStore((s) => s.authenticated)
  const email = useStore((s) => s.email)
  const hasKey = useStore((s) => s.hasKey)
  const authMessage = useStore((s) => s.authMessage)
  const provider = useStore((s) => s.provider)
  const busy = useStore((s) => s.busy)
  const login = useStore((s) => s.login)
  const logout = useStore((s) => s.logout)
  const saveKey = useStore((s) => s.saveKey)
  const deleteAccount = useStore((s) => s.deleteAccount)

  const [open, setOpen] = useState(false)
  const [emailText, setEmailText] = useState('')
  const [keyText, setKeyText] = useState('')

  return (
    <div class="account">
      <button
        class="icon-button"
        onClick={() => setOpen((v) => !v)}
        title={authenticated ? (email ?? 'Account') : 'Sign in / settings'}
      >
        <IconUser />
        {hasKey && <span class="key-dot" title="LLM key set" />}
      </button>

      {open && (
        <div class="account-panel">
          {!authenticated && (
            <div class="account-section">
              <div class="account-title">Sign in by email</div>
              <input
                type="email"
                placeholder="you@example.com"
                value={emailText}
                onInput={(e) => setEmailText((e.target as HTMLInputElement).value)}
              />
              <button
                class="primary"
                disabled={busy || !emailText.includes('@')}
                onClick={() => login(emailText.trim())}
              >
                Send link
              </button>
              {authMessage && <div class="account-note">{authMessage}</div>}
            </div>
          )}

          {authenticated && (
            <div class="account-section">
              <div class="account-title">{email}</div>
              <button class="text-link" disabled={busy} onClick={() => logout()}>
                Sign out
              </button>
              <button
                class="text-link danger"
                disabled={busy}
                onClick={() => {
                  if (confirm('Delete your account and all settings?')) deleteAccount()
                }}
              >
                Delete account
              </button>
            </div>
          )}

          <div class="account-section">
            <div class="account-title">LLM key ({provider})</div>
            <div class="account-note">
              {hasKey ? 'Key saved.' : 'No key set — generation is unavailable without one.'}
            </div>
            <input
              type="password"
              placeholder="sk-…"
              value={keyText}
              onInput={(e) => setKeyText((e.target as HTMLInputElement).value)}
            />
            <button
              class="primary"
              disabled={busy || !keyText.trim()}
              onClick={async () => {
                await saveKey(keyText.trim())
                setKeyText('')
              }}
            >
              Save key
            </button>
            {!authenticated && (
              <div class="account-note dim">Without signing in, the key is kept for this session only.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
