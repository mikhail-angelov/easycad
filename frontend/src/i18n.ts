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
    'app.saveProject': 'Save',
    'app.loadProject': 'Load',
    'app.newModel': 'New',
    'app.showCode': 'Show code',
    'app.hideCode': 'Hide code',
    'app.code': 'Code',
    'app.loadingEditor': 'Loading editor…',
    'app.loadingViewer': 'Loading viewer…',
    'feedback.tip': 'Send feedback',
    'feedback.title': 'Send feedback',
    'feedback.placeholder': 'What works, what’s missing, what broke?',
    'feedback.rateAria': 'Rate your experience',
    'feedback.emailPlaceholder': 'Email (optional, for a reply)',
    'feedback.send': 'Send',
    'feedback.sending': 'Sending…',
    'feedback.thanks': 'Thanks for the feedback! 🙏',
    'feedback.error': 'Could not send. Please try again.',

    'editor.title': 'Code',
    'editor.run': 'Run ▷',

    'viewer.title': 'Model',
    'viewer.wireframe': 'wireframe',
    'viewer.exportStl': 'Export STL',
    'viewer.download': 'Download',
    'viewer.hintMesh': '3D mesh',
    'viewer.hintCad': 'CAD solid',
    'viewer.hintSource': 'CadQuery',

    'geometry.size': 'Size',
    'geometry.topology': 'Topology',
    'geometry.mm': 'mm',
    'geometry.solids': '{n} solid(s)',
    'geometry.faces': '{n} faces',
    'geometry.edges': '{n} edges',
    'geometry.unavailable': 'Geometry unavailable',

    'timeline.steps': 'Steps',
    'timeline.github': 'View on GitHub',

    'chat.title': 'Chat',
    'chat.refine': 'Refine prompt',
    'chat.refineTip': 'Refine short prompts into precise instructions',
    'chat.working': 'Working…',
    'chat.stageThinking': 'Understanding your request…',
    'chat.stageGenerating': 'Generating geometry…',
    'chat.stageBuilding': 'Building the model…',
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
    'chat.variations': '3 variants',
    'chat.inputHint': 'Enter to send · Shift+Enter for a new line',
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
    'account.removeKey': 'Remove key',
    'account.provider': 'Provider',
    'account.model': 'Model',
    'account.validateSave': 'Validate & save key',
    'account.checking': 'Checking…',
    'account.saveAnyway': 'Save anyway',
    'account.keyVerified': 'Key verified and saved.',
    'account.sessionOnly': 'Without signing in, the key is kept for this session only.',
    'account.keyPrivacy':
      'Your key is encrypted at rest and used only to call your provider — never shared or logged. Replace or remove it anytime; deleting your account erases it.',
    'account.terms': 'Terms',
    'account.privacy': 'Privacy',

    'notice.signIn': 'Sign in',
    'notice.addKey': 'Add your key',
    'notice.dismiss': 'Dismiss',
    'notice.trial_exhausted_anon': 'Register for free generations, or add your own key.',
    'notice.trial_exhausted_user': "You've used your free generations — add your LLM key to continue.",
    'notice.trial_budget_exhausted': 'Free generations are paused right now — add your LLM key to keep building.',
    'notice.server_busy': "We're under heavy load right now — try again in a few seconds.",
    'notice.execution_timeout': 'That model took too long to build — simplify it or try again.',
    'notice.worker_unavailable': 'The modelling service is briefly unavailable — try again in a moment.',

    'account.keySet': 'LLM key set',
    'chat.inconsistent': 'Inconsistent request.',
    'store.loadProjectError': 'Could not load project: {error}',
    'lang.ariaLabel': 'Language',
  },
  ru: {
    'app.projectName': 'cadquery-чат',
    'app.saveProject': 'Сохранить',
    'app.loadProject': 'Загрузить',
    'app.newModel': 'Новая',
    'app.showCode': 'Показать код',
    'app.hideCode': 'Скрыть код',
    'app.code': 'Код',
    'app.loadingEditor': 'Загрузка редактора…',
    'app.loadingViewer': 'Загрузка вьюера…',
    'feedback.tip': 'Оставить отзыв',
    'feedback.title': 'Оставить отзыв',
    'feedback.placeholder': 'Что удобно, чего не хватает, что сломалось?',
    'feedback.rateAria': 'Оцените впечатление',
    'feedback.emailPlaceholder': 'Email (необязательно, для ответа)',
    'feedback.send': 'Отправить',
    'feedback.sending': 'Отправка…',
    'feedback.thanks': 'Спасибо за отзыв! 🙏',
    'feedback.error': 'Не удалось отправить. Попробуйте ещё раз.',

    'editor.title': 'Код',
    'editor.run': 'Запустить ▷',

    'viewer.title': 'Модель',
    'viewer.wireframe': 'каркас',
    'viewer.exportStl': 'Экспорт STL',
    'viewer.download': 'Скачать',
    'viewer.hintMesh': '3D-меш',
    'viewer.hintCad': 'CAD-модель',
    'viewer.hintSource': 'CadQuery',

    'geometry.size': 'Размер',
    'geometry.topology': 'Топология',
    'geometry.mm': 'мм',
    'geometry.solids': 'тел: {n}',
    'geometry.faces': 'граней: {n}',
    'geometry.edges': 'рёбер: {n}',
    'geometry.unavailable': 'Геометрия недоступна',

    'timeline.steps': 'Шаги',
    'timeline.github': 'Открыть на GitHub',

    'chat.title': 'Чат',
    'chat.refine': 'Уточнять запрос',
    'chat.refineTip': 'Уточнять короткие запросы до точных инструкций',
    'chat.working': 'Обработка…',
    'chat.stageThinking': 'Разбираю запрос…',
    'chat.stageGenerating': 'Генерирую геометрию…',
    'chat.stageBuilding': 'Собираю модель…',
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
    'chat.variations': '3 варианта',
    'chat.inputHint': 'Enter — отправить · Shift+Enter — новая строка',
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
    'account.removeKey': 'Удалить ключ',
    'account.provider': 'Провайдер',
    'account.model': 'Модель',
    'account.validateSave': 'Проверить и сохранить ключ',
    'account.checking': 'Проверка…',
    'account.saveAnyway': 'Всё равно сохранить',
    'account.keyVerified': 'Ключ проверен и сохранён.',
    'account.sessionOnly': 'Без входа ключ хранится только в текущей сессии.',
    'account.keyPrivacy':
      'Ключ хранится в зашифрованном виде и используется только для вызовов вашего провайдера — не передаётся и не логируется. Можно заменить или удалить в любой момент; удаление аккаунта стирает его.',
    'account.terms': 'Условия',
    'account.privacy': 'Конфиденциальность',

    'notice.signIn': 'Войти',
    'notice.addKey': 'Добавить ключ',
    'notice.dismiss': 'Скрыть',
    'notice.trial_exhausted_anon': 'Зарегистрируйтесь ради бесплатных генераций или добавьте свой ключ.',
    'notice.trial_exhausted_user': 'Бесплатные генерации закончились — добавьте свой ключ LLM, чтобы продолжить.',
    'notice.trial_budget_exhausted': 'Бесплатные генерации сейчас на паузе — добавьте свой ключ LLM, чтобы продолжить.',
    'notice.server_busy': 'Сейчас высокая нагрузка — попробуйте снова через несколько секунд.',
    'notice.execution_timeout': 'Модель строилась слишком долго — упростите её или попробуйте снова.',
    'notice.worker_unavailable': 'Сервис моделирования ненадолго недоступен — попробуйте через мгновение.',

    'account.keySet': 'Ключ LLM установлен',
    'chat.inconsistent': 'Несогласованный запрос.',
    'store.loadProjectError': 'Не удалось загрузить проект: {error}',
    'lang.ariaLabel': 'Язык',
  },
}
