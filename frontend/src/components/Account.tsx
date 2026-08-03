import { useEffect, useState } from 'preact/hooks'
import { ApiError, api, type CreatedToken, type TokenInfo } from '../api'
import { useStore, useT } from '../store'
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
  const removeKey = useStore((s) => s.removeKey)
  const validateKey = useStore((s) => s.validateKey)
  const deleteAccount = useStore((s) => s.deleteAccount)
  const t = useT()

  const [emailText, setEmailText] = useState('')
  const [keyText, setKeyText] = useState('')
  // Provider is chosen only here (in the key form) so we know the key type.
  const [provider, setProvider] = useState(savedProvider)
  const [model, setModel] = useState(savedModel || '')
  // Inline validation result for the key: { ok, reason } | null.
  const [result, setResult] = useState<{ ok: boolean; reason: string | null } | null>(null)
  const [checking, setChecking] = useState(false)
  // In-DOM delete confirmation (SPEC22 §4.3): a native browser dialog freezes
  // automation agents, so we use an inline two-button confirm instead.
  const [confirmDelete, setConfirmDelete] = useState(false)

  // Access tokens (SPEC22 §3.1). The raw secret of a just-created token is shown
  // once; the list otherwise carries no secrets.
  const [tokens, setTokens] = useState<TokenInfo[]>([])
  const [tokenName, setTokenName] = useState('')
  const [createdToken, setCreatedToken] = useState<CreatedToken | null>(null)
  const [copied, setCopied] = useState(false)
  const [tokenBusy, setTokenBusy] = useState(false)
  // Token ops don't touch the CAD automation state (they're not CAD mutations),
  // so a failure must NOT be silently swallowed — it surfaces here as a
  // machine-readable signal (`#account-token-error`) an orchestrating agent can
  // observe, complementing the success signal `#account-token-value` (SPEC22 §3.1).
  const [tokenError, setTokenError] = useState<string | null>(null)

  const refreshTokens = async () => {
    try {
      setTokens(await api.listTokens())
    } catch {
      /* not signed in / transient — leave the list as-is */
    }
  }

  // Load the token list whenever the panel opens for a signed-in user.
  useEffect(() => {
    if (open && authenticated) refreshTokens()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, authenticated])

  const createToken = async () => {
    setTokenBusy(true)
    setTokenError(null)
    // Drop any prior one-time secret so a failed retry can't leave a stale
    // `#account-token-value` next to `#account-token-error` — the "wait for one"
    // completion contract (docs/automation.md) requires exactly one to be present.
    setCreatedToken(null)
    try {
      const tok = await api.createToken(tokenName.trim() || 'token')
      setCreatedToken(tok)
      setCopied(false)
      setTokenName('')
      await refreshTokens()
    } catch (e) {
      // e.g. 429 at the 10-token cap or the per-user rate limit.
      setTokenError(e instanceof ApiError ? e.message : t('account.tokenOpError'))
    } finally {
      setTokenBusy(false)
    }
  }

  const copyToken = async () => {
    if (!createdToken) return
    try {
      await navigator.clipboard.writeText(createdToken.token)
      setCopied(true)
    } catch {
      /* clipboard blocked — the value stays visible for manual copy */
    }
  }

  const revoke = async (id: number) => {
    setTokenError(null)
    setTokenBusy(true)
    try {
      await api.revokeToken(id)
      // Mark inactive locally so the revoke button disappears immediately — the
      // documented completion signal — regardless of the best-effort refresh
      // below. An agent then never hangs waiting on a refresh that silently failed.
      setTokens((prev) => prev.map((tok) => (tok.id === id ? { ...tok, revoked_at: Math.floor(Date.now() / 1000) } : tok)))
      await refreshTokens()
    } catch (e) {
      setTokenError(e instanceof ApiError ? e.message : t('account.tokenOpError'))
    } finally {
      setTokenBusy(false)
    }
  }

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
        id="account-toggle"
        data-testid="account-toggle"
        aria-label={authenticated ? (email ?? t('account.iconTip')) : t('account.iconTip')}
        onClick={() => setOpen(!open)}
        title={authenticated ? (email ?? t('account.iconTip')) : t('account.iconTip')}
      >
        <IconUser />
        {hasKey && <span class="key-dot" title={t('account.keySet')} />}
      </button>

      {open && (
        <div class="account-panel">
          {!authenticated && (
            <div class="account-section">
              <div class="account-title">{t('account.signInTitle')}</div>
              <input
                type="email"
                name="login-email"
                data-testid="account-login-email"
                placeholder="you@example.com"
                value={emailText}
                onInput={(e) => setEmailText((e.target as HTMLInputElement).value)}
              />
              <button
                class="primary"
                id="account-login-submit"
                data-testid="account-login-submit"
                disabled={busy || !emailText.includes('@')}
                onClick={() => login(emailText.trim())}
              >
                {t('account.sendLink')}
              </button>
              {authMessage && <div class="account-note">{t('account.linkSent', { email: authMessage })}</div>}
            </div>
          )}

          {authenticated && (
            <div class="account-section">
              <div class="account-title">{email}</div>
              <button id="account-logout" class="text-link" disabled={busy} onClick={() => logout()}>
                {t('account.signOut')}
              </button>
              {!confirmDelete ? (
                <button
                  class="text-link danger"
                  id="account-delete"
                  disabled={busy}
                  onClick={() => setConfirmDelete(true)}
                >
                  {t('account.delete')}
                </button>
              ) : (
                <div class="account-confirm">
                  <span>{t('account.deleteConfirm')}</span>
                  <button
                    class="text-link danger"
                    id="account-delete-confirm"
                    disabled={busy}
                    onClick={() => {
                      setConfirmDelete(false)
                      deleteAccount()
                    }}
                  >
                    {t('account.delete')}
                  </button>
                  <button id="account-delete-cancel" class="text-link" disabled={busy} onClick={() => setConfirmDelete(false)}>
                    {t('chat.cancel')}
                  </button>
                </div>
              )}
            </div>
          )}

          {authenticated && (
            <div class="account-section">
              <div class="account-title">{t('account.tokensTitle')}</div>
              <div class="account-note dim">{t('account.tokensHint')}</div>
              <input
                type="text"
                id="account-token-name"
                name="token-name"
                placeholder={t('account.tokenNamePlaceholder')}
                value={tokenName}
                disabled={tokenBusy}
                onInput={(e) => setTokenName((e.target as HTMLInputElement).value)}
              />
              <button id="account-token-create" class="primary" disabled={tokenBusy} onClick={() => createToken()}>
                {t('account.tokenCreate')}
              </button>

              {tokenError && (
                <div id="account-token-error" class="account-warn" role="alert">
                  {tokenError}
                </div>
              )}

              {createdToken && (
                <div class="account-token-new">
                  <input
                    id="account-token-value"
                    name="token-value"
                    readonly
                    value={createdToken.token}
                    onFocus={(e) => (e.target as HTMLInputElement).select()}
                  />
                  <button id="account-token-copy" class="text-link" onClick={() => copyToken()}>
                    {copied ? t('account.tokenCopied') : t('account.tokenCopy')}
                  </button>
                  <div class="account-note dim">{t('account.tokenValueHint')}</div>
                </div>
              )}

              {tokens.length > 0 && (
                <ul class="account-token-list">
                  {tokens.map((tok) => {
                    const revoked = tok.revoked_at != null
                    const expired = !revoked && tok.expires_at * 1000 < Date.now()
                    const inactive = revoked || expired
                    return (
                      <li key={tok.id} class={`account-token-item ${inactive ? 'inactive' : ''}`}>
                        <span class="account-token-label">
                          {tok.name}
                          {revoked && ` (${t('account.tokenRevoked')})`}
                          {expired && ` (${t('account.tokenExpired')})`}
                        </span>
                        {!inactive && (
                          <button id={`token-revoke-${tok.id}`} class="text-link danger" disabled={tokenBusy} onClick={() => revoke(tok.id)}>
                            {t('account.tokenRevoke')}
                          </button>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          )}

          <div class="account-section">
            <div class="account-title">{t('account.keyTitle')}</div>
            <div class="account-note">
              {hasKey
                ? t('account.keySaved', { provider: savedProvider })
                : t('account.keyPrompt')}
            </div>
            {hasKey && (
              <button id="account-remove-key" class="text-link" disabled={busy} onClick={() => removeKey()}>
                {t('account.removeKey')}
              </button>
            )}

            <label class="account-field">
              <span>{t('account.provider')}</span>
              <select
                name="provider"
                data-testid="account-provider"
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
              <span>{t('account.model')}</span>
              <select name="model" data-testid="account-model" value={model} disabled={busy} onChange={(e) => setModel((e.target as HTMLSelectElement).value)}>
                {models.map((mo) => (
                  <option value={mo} key={mo}>
                    {mo}
                  </option>
                ))}
              </select>
            </label>

            <input
              type="password"
              name="api-key"
              data-testid="account-api-key"
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
                <button id="account-save-anyway" class="text-link" disabled={busy} onClick={() => onSave(true)}>
                  {t('account.saveAnyway')}
                </button>
              </div>
            )}
            {result && result.ok && <div class="account-ok">{t('account.keyVerified')}</div>}

            <button id="account-save-key" class="primary" disabled={busy || checking || !keyText.trim()} onClick={() => onSave()}>
              {checking ? t('account.checking') : t('account.validateSave')}
            </button>

            <div class="account-note dim">
              {authenticated ? t('account.keyPrivacy') : t('account.sessionOnly')}
            </div>
          </div>

          <div class="account-legal">
            <a id="account-terms" href="/terms" target="_blank" rel="noopener">{t('account.terms')}</a>
            <span aria-hidden="true"> · </span>
            <a id="account-privacy" href="/privacy" target="_blank" rel="noopener">{t('account.privacy')}</a>
          </div>
        </div>
      )}
    </div>
  )
}
