import { test, expect, type Browser, type BrowserContext, type Page } from '@playwright/test'

// SPEC22 §4.4 browser acceptance. A real headless run is the only thing that
// proves the PAT→cookie exchange, cookie/session, data-state transitions and STL
// download work end-to-end. Source tests are necessary but not sufficient.
//
// Prereqs: a running server (EASYCAD_URL) with JWT_SECRET set. Tests that mint a
// real PAT need a provisioned account cookie (EASYCAD_AUTH_COOKIE) and are skipped
// otherwise. The full generate→export turn additionally needs a working backend
// LLM key and is gated on EASYCAD_PAT_LLM=1.

const APP = '/app'

function origin(baseURL: string) {
  return new URL(baseURL).origin
}

/** Seed a PAT into the app origin's sessionStorage via a document-start init
 *  script (shape A), with the mandatory origin + top-frame + single-use guards. */
async function seedPat(context: BrowserContext, token: string, appOrigin: string) {
  await context.addInitScript(
    ({ token, appOrigin }) => {
      if (window.top !== window) return
      if (location.origin !== appOrigin) return
      if (sessionStorage.getItem('easycad_pat_used')) return
      sessionStorage.setItem('easycad_pat_used', '1')
      sessionStorage.setItem('easycad_pat', token)
    },
    { token, appOrigin },
  )
}

/** Mint a PAT via the API, reusing a signed-in context's cookie. */
async function mintToken(context: BrowserContext, name = 'e2e'): Promise<string> {
  const res = await context.request.post('/api/tokens', { data: { name } })
  expect(res.ok(), `create token: ${res.status()}`).toBeTruthy()
  const body = await res.json()
  expect(body.token).toMatch(/^pat_/)
  return body.token
}

/** A fresh context pre-loaded with a provisioned account's auth cookie so we can
 *  mint tokens via the API. */
async function authedContext(browser: Browser, baseURL: string, cookie = process.env.EASYCAD_AUTH_COOKIE!): Promise<BrowserContext> {
  const context = await browser.newContext()
  await context.addCookies([{ name: 'auth_token', value: cookie, url: baseURL }])
  return context
}

/** The signed-in email for a context (via /api/auth/me). */
async function emailOf(context: BrowserContext): Promise<string | null> {
  const me = await (await context.request.get('/api/auth/me')).json()
  return me.email ?? null
}

/** Count POST /api/auth/token requests over the page's lifetime. */
function trackExchanges(page: Page): { count: () => number } {
  let n = 0
  page.on('request', (r) => {
    if (r.url().includes('/api/auth/token') && r.method() === 'POST') n += 1
  })
  return { count: () => n }
}

test.describe('PAT bootstrap — failure & isolation (no account needed)', () => {
  test('bad PAT → free trial + neutral auth-error, no token echoed', async ({ browser, baseURL }) => {
    const context = await browser.newContext() // fresh context — mandatory
    await seedPat(context, 'pat_totally-invalid', origin(baseURL!))
    const page = await context.newPage()
    await page.goto(APP)

    const root = page.locator('[data-state]')
    await expect(root).toHaveAttribute('data-auth-error', 'invalid-token')
    await expect(page.locator('#auth-error-banner')).toBeVisible()
    await expect(page.locator('#chat-prompt')).toBeVisible() // renders on the trial
    expect(await page.content()).not.toContain('pat_totally-invalid') // never echoed
    await page.click('#auth-error-dismiss')
    await expect(page.locator('#auth-error-banner')).toHaveCount(0)
    await context.close()
  })

  test('reload does not re-seed / re-exchange, even after a failed exchange', async ({ browser, baseURL }) => {
    const context = await browser.newContext()
    await seedPat(context, 'pat_invalid-once', origin(baseURL!))
    const page = await context.newPage()
    const ex = trackExchanges(page)
    await page.goto(APP)
    await expect(page.locator('[data-auth-error]')).toHaveAttribute('data-auth-error', 'invalid-token')
    await page.reload()
    await page.waitForTimeout(500) // give any erroneous second exchange a chance
    expect(ex.count()).toBe(1) // exactly once despite the reload + failed first try
    expect(await page.evaluate(() => sessionStorage.getItem('easycad_pat'))).toBeNull()
    await context.close()
  })

  test('hung exchange is bounded — SPA still renders (5s AbortSignal.timeout)', async ({ browser, baseURL }) => {
    const context = await browser.newContext()
    await seedPat(context, 'pat_hangs-forever', origin(baseURL!))
    const page = await context.newPage()
    // Never fulfil the exchange (and swallow the non-awaited logout too).
    await page.route('**/api/auth/token', () => {})
    await page.route('**/api/auth/logout', () => {})
    await page.goto(APP, { waitUntil: 'domcontentloaded' })
    // Despite the hung request, the render happens within the timeout budget.
    await expect(page.locator('#chat-prompt')).toBeVisible({ timeout: 15_000 })
    await expect(page.locator('[data-auth-error]')).toHaveAttribute('data-auth-error', 'invalid-token')
    await context.close()
  })

  test('cross-origin iframe is never seeded', async ({ browser, baseURL }) => {
    const context = await browser.newContext()
    await seedPat(context, 'pat_frame-secret', origin(baseURL!))
    const page = await context.newPage()
    await page.setContent(`<iframe src="https://example.com"></iframe>`, { waitUntil: 'domcontentloaded' })
    for (const frame of page.frames()) {
      const v = await frame
        .evaluate(() => {
          try {
            return sessionStorage.getItem('easycad_pat')
          } catch {
            return null
          }
        })
        .catch(() => null)
      expect(v).toBeNull()
    }
    await context.close()
  })
})

test.describe('PAT identity + state (needs a provisioned account)', () => {
  test.skip(!process.env.EASYCAD_AUTH_COOKIE, 'set EASYCAD_AUTH_COOKIE to run')

  test('valid PAT → authenticated; reset is a fast-completion turn whose rev-based wait resolves', async ({ browser, baseURL }) => {
    const owner = await authedContext(browser, baseURL!)
    const ownerEmail = await emailOf(owner)
    const token = await mintToken(owner)

    const agent = await browser.newContext() // fresh
    await seedPat(agent, token, origin(baseURL!))
    const page = await agent.newPage()
    await page.goto(APP)

    // Signed in AS THE OWNER (the account panel shows the owner's email).
    await page.click('#account-toggle')
    await expect(page.locator('#account-login-submit')).toHaveCount(0)
    await expect(page.locator('.account-title').first()).toHaveText(ownerEmail!)

    // Reset is a near-instant turn that may never paint `generating`. The
    // documented rev-based wait must still resolve (SPEC22 §4.1 fast-completion).
    const root = page.locator('[data-state]')
    const rev0 = Number(await root.getAttribute('data-state-rev'))
    await page.click('#project-new')
    await page.waitForFunction((prev) => {
      const el = document.querySelector('[data-state]')!
      const r = Number(el.getAttribute('data-state-rev'))
      const s = el.getAttribute('data-state')
      return r > prev && ['idle', 'done', 'error', 'awaiting-input'].includes(s!)
    }, rev0)
    await expect(root).toHaveAttribute('data-state', 'idle')

    await owner.close()
    await agent.close()
  })

  test('reused context with a stale auth_token: failed PAT exchange logs out, not signed-in-as-wrong-user', async ({ browser, baseURL }) => {
    // A context that already holds user A's cookie…
    const reused = await authedContext(browser, baseURL!)
    // …then attempts to bootstrap an INVALID PAT. The boot code's best-effort
    // logout must clear the stale cookie so the agent is anonymous, not A.
    await seedPat(reused, 'pat_invalid-in-reused-ctx', origin(baseURL!))
    const page = await reused.newPage()
    await page.goto(APP)
    await expect(page.locator('[data-auth-error]')).toHaveAttribute('data-auth-error', 'invalid-token')
    await page.click('#account-toggle')
    // The login form is present → anonymous, NOT signed in as A. (This is
    // defence-in-depth; the real guarantee for agents is a fresh context.)
    await expect(page.locator('#account-login-submit')).toBeVisible()
    await reused.close()
  })

  test('two-user, fresh contexts: B is B (not A) and inherits none of A settings/steps', async ({ browser, baseURL }) => {
    test.skip(!process.env.EASYCAD_AUTH_COOKIE_B, 'set EASYCAD_AUTH_COOKIE_B (a second account) to run')

    const ownerA = await authedContext(browser, baseURL!)
    const emailA = await emailOf(ownerA)
    const tokenA = await mintToken(ownerA, 'a')
    // Give A real, observable state via ownerA.request (it already holds A's auth
    // cookie, so no browser-boot race): a user-scoped BYOK key + a session-scoped
    // non-initial CAD step. ownerA's first request is assigned its own easycad_session.
    await ownerA.request.put('/api/settings', { data: { key: 'sk-user-A-secret' } })
    await ownerA.request.post('/api/execute-manual', {
      data: { code: 'import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 10)\n' },
    })
    const sessA = await (await ownerA.request.get('/api/session')).json()
    expect(sessA.settings.has_key, 'A should now have a key').toBe(true)
    expect(sessA.steps.some((s: { kind: string }) => s.kind === 'manual'), 'A should have a non-initial (manual) step').toBe(true)

    // B bootstraps its OWN PAT in its OWN fresh context.
    const ownerB = await authedContext(browser, baseURL!, process.env.EASYCAD_AUTH_COOKIE_B!)
    const emailB = await emailOf(ownerB)
    expect(emailB, 'B must be a different account than A').not.toBe(emailA)
    const tokenB = await mintToken(ownerB, 'b')
    const ctxB = await browser.newContext()
    await seedPat(ctxB, tokenB, origin(baseURL!))
    const pageB = await ctxB.newPage()
    await pageB.goto(APP)

    // Identity: B is signed in as B, never A.
    await pageB.click('#account-toggle')
    await expect(pageB.locator('.account-title').first()).toHaveText(emailB!)
    await expect(pageB.locator('.account-title').first()).not.toHaveText(emailA!)

    // Settings isolation (user-scoped): read as B authoritatively (ownerB holds
    // B's cookie) → B has no key, so A's saved key never leaked across users.
    const settingsB = await (await ownerB.request.get('/api/settings')).json()
    expect(settingsB.has_key, 'B (the user) must not inherit A key').toBe(false)
    // Steps isolation (session-scoped): read the ACTUAL fresh B browsing session
    // via the page's own cookies → only the initial step, none of A's.
    const sessB = await pageB.evaluate(() => fetch('/api/session').then((r) => r.json()))
    expect(sessB.steps.some((s: { kind: string }) => s.kind !== 'initial'), 'B must not inherit any of A non-initial steps').toBe(false)
    expect(['idle', 'done']).toContain(await pageB.locator('[data-state]').getAttribute('data-state'))

    await ownerA.close()
    await ownerB.close()
    await ctxB.close()
  })

  // LAST in this block: it permanently deletes account A, so it must run after
  // every other test that mints with A's cookie.
  test('deleted account: a second context holding the PAT cookie becomes anonymous', async ({ browser, baseURL }) => {
    const owner = await authedContext(browser, baseURL!)
    const token = await mintToken(owner, 'to-be-orphaned')

    // Agent bootstraps the PAT in a fresh context and is authenticated.
    const agent = await browser.newContext()
    await seedPat(agent, token, origin(baseURL!))
    const page = await agent.newPage()
    await page.goto(APP)
    await page.click('#account-toggle')
    await expect(page.locator('#account-login-submit')).toHaveCount(0)

    // Owner deletes the account out-of-band.
    expect((await owner.request.delete('/api/auth/me')).ok()).toBeTruthy()

    // The agent's still-present cookie now resolves anonymous on the next load.
    await page.goto(APP)
    await page.click('#account-toggle')
    await expect(page.locator('#account-login-submit')).toBeVisible()

    await owner.close()
    await agent.close()
  })
})

test.describe('full turn: create → export STL (needs a live LLM)', () => {
  test.skip(process.env.EASYCAD_PAT_LLM !== '1', 'set EASYCAD_PAT_LLM=1 with a working backend key')

  test('prompt → done (rev-based wait) → download STL', async ({ browser, baseURL }) => {
    const token = process.env.EASYCAD_PAT!
    const context = await browser.newContext()
    await seedPat(context, token, origin(baseURL!))
    const page = await context.newPage()
    await page.goto(APP)

    const root = page.locator('[data-state]')
    const rev = Number(await root.getAttribute('data-state-rev'))
    await page.fill('#chat-prompt', 'a 20mm cube')
    await page.click('#chat-send')
    await page.waitForFunction((prev) => {
      const el = document.querySelector('[data-state]')!
      const r = Number(el.getAttribute('data-state-rev'))
      const s = el.getAttribute('data-state')
      return r > prev && ['done', 'error', 'awaiting-input'].includes(s!)
    }, rev)
    await expect(root).toHaveAttribute('data-state', 'done')

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      (async () => {
        await page.click('#viewer-download')
        await page.click('#export-stl')
      })(),
    ])
    expect(await download.path()).toBeTruthy()
    await context.close()
  })
})
