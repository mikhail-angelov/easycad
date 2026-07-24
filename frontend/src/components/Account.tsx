import { useEffect, useState } from 'preact/hooks'
import { useStore } from '../store'
import { IconUser } from './Icons'

export function Account() {
  const authenticated = useStore((s) => s.authenticated)
  const email = useStore((s) => s.email)
  const hasKey = useStore((s) => s.hasKey)
  const authMessage = useStore((s) => s.authMessage)
  const providers = useStore((s) => s.providers)
  const savedProvider = useStore((s) => s.provider)
  const savedModel = useStore((s) => s.model)
  const busy = useStore((s) => s.busy)
  const open = useStore((s) => s.accountOpen)
  const setOpen = useStore((s) => s.setAccountOpen)
  const login = useStore((s) => s.login)
  const logout = useStore((s) => s.logout)
  const saveKey = useStore((s) => s.saveKey)
  const validateKey = useStore((s) => s.validateKey)
  const deleteAccount = useStore((s) => s.deleteAccount)

  const [emailText, setEmailText] = useState('')
  const [keyText, setKeyText] = useState('')
  // Provider is chosen only here (in the key form) so we know the key type.
  const [provider, setProvider] = useState(savedProvider)
  const [model, setModel] = useState(savedModel || '')
  // Inline validation result for the key: { ok, reason } | null.
  const [result, setResult] = useState<{ ok: boolean; reason: string | null } | null>(null)
  const [checking, setChecking] = useState(false)

  const providerNames = Object.keys(providers)
  const models = providers[provider]?.models ?? []

  // Default the model to the provider's default whenever the provider changes.
  useEffect(() => {
    const def = providers[provider]?.default_model
    if (def && !models.includes(model)) setModel(def)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, providers])

  const onSave = async (skipValidation = false) => {
    const key = keyText.trim()
    if (!key) return
    setResult(null)
    if (!skipValidation) {
      setChecking(true)
      const res = await validateKey(provider, key)
      setChecking(false)
      setResult(res)
      if (!res.ok) return // show the orange reason; user may "Save anyway"
    }
    await saveKey(provider, model, key)
    setKeyText('')
    setResult({ ok: true, reason: null })
  }

  return (
    <div class="account">
      <button
        class="icon-button"
        onClick={() => setOpen(!open)}
        title={authenticated ? (email ?? 'Account') : 'Account & LLM key'}
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
            <div class="account-title">Your LLM key</div>
            <div class="account-note">
              {hasKey
                ? `Key saved (${savedProvider}). Add a new one to replace it.`
                : 'Add a key for unlimited generations with your own model choice.'}
            </div>

            <label class="account-field">
              <span>Provider</span>
              <select
                value={provider}
                disabled={busy}
                onChange={(e) => {
                  setProvider((e.target as HTMLSelectElement).value)
                  setResult(null)
                }}
              >
                {providerNames.map((p) => (
                  <option value={p} key={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>

            <label class="account-field">
              <span>Model</span>
              <select value={model} disabled={busy} onChange={(e) => setModel((e.target as HTMLSelectElement).value)}>
                {models.map((mo) => (
                  <option value={mo} key={mo}>
                    {mo}
                  </option>
                ))}
              </select>
            </label>

            <input
              type="password"
              placeholder={providers[provider]?.key_prefix ? `${providers[provider].key_prefix}…` : 'sk-…'}
              value={keyText}
              onInput={(e) => {
                setKeyText((e.target as HTMLInputElement).value)
                setResult(null)
              }}
            />

            {result && !result.ok && (
              <div class="account-warn">
                {result.reason}
                <button class="text-link" disabled={busy} onClick={() => onSave(true)}>
                  Save anyway
                </button>
              </div>
            )}
            {result && result.ok && <div class="account-ok">Key verified and saved.</div>}

            <button class="primary" disabled={busy || checking || !keyText.trim()} onClick={() => onSave()}>
              {checking ? 'Checking…' : 'Validate & save key'}
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
