import { useState } from 'preact/hooks'
import { useStore } from '../store'

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

  const label = authenticated ? (email ?? 'Аккаунт') : 'Войти'

  return (
    <div class="account">
      <button class="text-button" onClick={() => setOpen((v) => !v)}>
        {label} {hasKey ? '· 🔑' : ''}
      </button>

      {open && (
        <div class="account-panel">
          {!authenticated && (
            <div class="account-section">
              <div class="account-title">Вход по ссылке</div>
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
                Отправить ссылку
              </button>
              {authMessage && <div class="account-note">{authMessage}</div>}
            </div>
          )}

          {authenticated && (
            <div class="account-section">
              <div class="account-title">{email}</div>
              <button class="text-button" disabled={busy} onClick={() => logout()}>
                Выйти
              </button>
              <button
                class="text-button danger"
                disabled={busy}
                onClick={() => {
                  if (confirm('Удалить аккаунт и все настройки?')) deleteAccount()
                }}
              >
                Удалить аккаунт
              </button>
            </div>
          )}

          <div class="account-section">
            <div class="account-title">LLM-ключ ({provider})</div>
            <div class="account-note">
              {hasKey ? 'Ключ сохранён.' : 'Ключ не задан — генерация недоступна без него.'}
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
              Сохранить ключ
            </button>
            {!authenticated && (
              <div class="account-note dim">Без входа ключ хранится только для этой сессии.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
