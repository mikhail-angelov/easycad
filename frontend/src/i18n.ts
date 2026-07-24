// Lightweight bilingual (en/ru) UI strings for the SPA.
//
// Default is English; Russian is used when the user picked it or the browser
// locale is Russian. The choice persists under the SAME localStorage key as the
// marketing landing (`easycad_lang`), so a language chosen on either surface
// carries over to the other. Pure module — no store import (the `useT` hook
// lives in store.ts to avoid a circular dependency).

export type Lang = 'en' | 'ru'
export const LANG_KEY = 'easycad_lang'

// localStorage choice wins; otherwise follow the browser (ru → ru, else en).
export function detectLang(): Lang {
  try {
    const saved = localStorage.getItem(LANG_KEY)
    if (saved === 'en' || saved === 'ru') return saved
  } catch {
    /* ignore */
  }
  const langs = navigator.languages || [navigator.language || 'en']
  for (const l of langs) {
    if (/^ru\b/i.test(l)) return 'ru'
    if (/^en\b/i.test(l)) return 'en'
  }
  return 'en'
}

type Params = Record<string, string | number>

export function translate(lang: Lang, key: string, params?: Params): string {
  const s = STRINGS[lang][key] ?? STRINGS.en[key] ?? key
  return params ? s.replace(/\{(\w+)\}/g, (_, k) => String(params[k] ?? `{${k}}`)) : s
}

// One-tap example prompts (localized so a RU user sends RU and gets RU back).
export const STARTERS: Record<Lang, string[]> = {
  en: [
    'Make it 10 mm thinner',
    'Add a 6 mm hole in each corner',
    'Round the top edges with a 3 mm fillet',
    'Hollow it out with 2 mm walls',
  ],
  ru: [
    'Сделай на 10 мм тоньше',
    'Добавь отверстие 6 мм в каждом углу',
    'Скругли верхние рёбра радиусом 3 мм',
    'Сделай полым со стенками 2 мм',
  ],
}

const STRINGS: Record<Lang, Record<string, string>> = {
  en: {
    'app.projectName': 'cadquery chat',
    'app.saveProject': 'Save project',
    'app.loadProject': 'Load project',
    'app.newModel': 'New model',
    'app.loadingEditor': 'Loading editor…',
    'app.loadingViewer': 'Loading viewer…',

    'editor.title': 'Code',
    'editor.run': 'Run ▷',

    'viewer.title': 'Model',
    'viewer.wireframe': 'wireframe',
    'viewer.exportStl': 'Export STL',

    'timeline.steps': 'Steps',
    'timeline.github': 'View on GitHub',

    'chat.title': 'Chat',
    'chat.refine': 'refine',
    'chat.refineTip': 'Refine short prompts into precise instructions',
    'chat.working': 'Working…',
    'chat.trialNoSignup': '{n} free · no sign-up',
    'chat.trialLeft': '{n} free left',
    'chat.trialAnonTip': 'Free generations — no sign-up needed. Sign in for more.',
    'chat.trialUserTip': 'Free generations remaining on your account.',
    'chat.welcomeTitle': '👋 Describe what you want to build',
    'chat.welcomeBody':
      'Type a change and the model updates. No sign-up needed to try — your first build is free.',
    'chat.dismiss': 'Dismiss',
    'chat.emptyHint': 'Describe one change at a time. Try one of these to start:',
    'chat.refinedPrompt': 'refined prompt',
    'chat.stepOk': 'Step {id} ✓',
    'chat.failed': 'Failed: {error}',
    'chat.proposalHead': 'Refined instruction — confirm or edit:',
    'chat.use': 'Use',
    'chat.cancel': 'Cancel',
    'chat.generateAnyway': 'Generate anyway',
    'chat.variationsHead': 'Pick a variation — click to preview in the viewer:',
    'chat.variationFailed': 'failed: {error}',
    'chat.useThis': 'Use this',
    'chat.inputPlaceholder': 'Describe a change…',
    'chat.send': 'Send',
    'chat.variationsTip': 'Generate 3 variations to pick from',
    'chat.modelTip': 'Model ({provider})',

    'account.iconTip': 'Account & LLM key',
    'account.signInTitle': 'Sign in by email',
    'account.sendLink': 'Send link',
    'account.signOut': 'Sign out',
    'account.delete': 'Delete account',
    'account.deleteConfirm': 'Delete your account and all settings?',
    'account.linkSent': 'We sent a sign-in link to {email}',
    'account.keyTitle': 'Your LLM key',
    'account.keySaved': 'Key saved ({provider}). Add a new one to replace it.',
    'account.keyPrompt': 'Add a key for unlimited generations with your own model choice.',
    'account.provider': 'Provider',
    'account.model': 'Model',
    'account.validateSave': 'Validate & save key',
    'account.checking': 'Checking…',
    'account.saveAnyway': 'Save anyway',
    'account.keyVerified': 'Key verified and saved.',
    'account.sessionOnly': 'Without signing in, the key is kept for this session only.',

    'notice.signIn': 'Sign in',
    'notice.addKey': 'Add your key',
    'notice.dismiss': 'Dismiss',
    'notice.trial_exhausted_anon': 'Register for free generations, or add your own key.',
    'notice.trial_exhausted_user': "You've used your free generations — add your LLM key to continue.",

    'account.keySet': 'LLM key set',
    'chat.inconsistent': 'Inconsistent request.',
    'store.loadProjectError': 'Could not load project: {error}',
    'lang.ariaLabel': 'Language',
  },
  ru: {
    'app.projectName': 'cadquery-чат',
    'app.saveProject': 'Сохранить проект',
    'app.loadProject': 'Загрузить проект',
    'app.newModel': 'Новая модель',
    'app.loadingEditor': 'Загрузка редактора…',
    'app.loadingViewer': 'Загрузка вьюера…',

    'editor.title': 'Код',
    'editor.run': 'Запустить ▷',

    'viewer.title': 'Модель',
    'viewer.wireframe': 'каркас',
    'viewer.exportStl': 'Экспорт STL',

    'timeline.steps': 'Шаги',
    'timeline.github': 'Открыть на GitHub',

    'chat.title': 'Чат',
    'chat.refine': 'уточнять',
    'chat.refineTip': 'Уточнять короткие запросы до точных инструкций',
    'chat.working': 'Обработка…',
    'chat.trialNoSignup': '{n} бесплатно · без регистрации',
    'chat.trialLeft': 'осталось {n}',
    'chat.trialAnonTip': 'Бесплатные генерации — без регистрации. Войдите, чтобы получить больше.',
    'chat.trialUserTip': 'Осталось бесплатных генераций на аккаунте.',
    'chat.welcomeTitle': '👋 Опишите, что хотите построить',
    'chat.welcomeBody':
      'Опишите изменение — модель обновится. Регистрация не нужна: первая генерация бесплатна.',
    'chat.dismiss': 'Скрыть',
    'chat.emptyHint': 'Описывайте по одному изменению за раз. Попробуйте для начала:',
    'chat.refinedPrompt': 'уточнённый запрос',
    'chat.stepOk': 'Шаг {id} ✓',
    'chat.failed': 'Ошибка: {error}',
    'chat.proposalHead': 'Уточнённая инструкция — подтвердите или измените:',
    'chat.use': 'Применить',
    'chat.cancel': 'Отмена',
    'chat.generateAnyway': 'Всё равно сгенерировать',
    'chat.variationsHead': 'Выберите вариант — нажмите, чтобы посмотреть во вьюере:',
    'chat.variationFailed': 'ошибка: {error}',
    'chat.useThis': 'Выбрать этот',
    'chat.inputPlaceholder': 'Опишите изменение…',
    'chat.send': 'Отправить',
    'chat.variationsTip': 'Сгенерировать 3 варианта на выбор',
    'chat.modelTip': 'Модель ({provider})',

    'account.iconTip': 'Аккаунт и ключ LLM',
    'account.signInTitle': 'Вход по email',
    'account.sendLink': 'Отправить ссылку',
    'account.signOut': 'Выйти',
    'account.delete': 'Удалить аккаунт',
    'account.deleteConfirm': 'Удалить аккаунт и все настройки?',
    'account.linkSent': 'Мы отправили ссылку для входа на {email}',
    'account.keyTitle': 'Ваш ключ LLM',
    'account.keySaved': 'Ключ сохранён ({provider}). Добавьте новый, чтобы заменить.',
    'account.keyPrompt': 'Добавьте ключ для безлимитных генераций с выбором своей модели.',
    'account.provider': 'Провайдер',
    'account.model': 'Модель',
    'account.validateSave': 'Проверить и сохранить ключ',
    'account.checking': 'Проверка…',
    'account.saveAnyway': 'Всё равно сохранить',
    'account.keyVerified': 'Ключ проверен и сохранён.',
    'account.sessionOnly': 'Без входа ключ хранится только в текущей сессии.',

    'notice.signIn': 'Войти',
    'notice.addKey': 'Добавить ключ',
    'notice.dismiss': 'Скрыть',
    'notice.trial_exhausted_anon': 'Зарегистрируйтесь ради бесплатных генераций или добавьте свой ключ.',
    'notice.trial_exhausted_user': 'Бесплатные генерации закончились — добавьте свой ключ LLM, чтобы продолжить.',

    'account.keySet': 'Ключ LLM установлен',
    'chat.inconsistent': 'Несогласованный запрос.',
    'store.loadProjectError': 'Не удалось загрузить проект: {error}',
    'lang.ariaLabel': 'Язык',
  },
}
