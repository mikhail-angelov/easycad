# Code review — незакоммиченные изменения

Проверено относительно `HEAD`.

## Standards

### P1 — `--max-cost` не ограничивает прогон и искажает частичный результат

`ProductSession` всегда возвращает `cost_usd=0`, поэтому счётчик не достигает
`--max-cost`. Если сценарии всё же не были запущены, grader пропускает их и
может показать 100% только по ранним сценариям. Нужны источник/оценка стоимости
до запроса и отдельное представление незапущенных сценариев в частичном прогоне.

Файлы: `bench/src/bench/backend.py`, `bench/src/bench/run.py`,
`bench/src/bench/grade.py`.

### P1 — коллизия идентификаторов запусков

ID содержит лишь минуту, а каталог создаётся с `exist_ok=True`. Повторный запуск
того же набора в одну минуту смешает артефакты и manifest двух прогонов. Нужен
уникальный суффикс (секунды/микросекунды/UUID) либо явная ошибка, если каталог
уже существует.

Файл: `bench/src/bench/run.py`.

### P1 — CI не требует ручной приёмки reference-моделей

`bench spec --check` проверяет факты и SHA исходника, но не `validation.json`.
Поэтому CI проходит без обязательной визуальной валидации эталонов.

Файл: `bench/src/bench/spec.py`.

### P2 — параметр `--seed` не влияет на прогон

Значение попадает только в manifest. Оно не передаётся backend и не используется
при surface sampling, хотя manifest создаёт впечатление обратного. Удалить флаг
или явно провести seed к месту, где он реально применяется.

Файлы: `bench/src/bench/run.py`, `bench/src/bench/grade.py`.

## Spec

### P1 — экспортированные артефакты не проверяются по SHA-256

Product backend просто скачивает STEP/STL. Спека требует URL, SHA-256 и
верификацию, иначе устаревший или неполный артефакт можно измерить как текущий.

Файл: `bench/src/bench/backend.py`.

### P1 — manifest недостаточен для воспроизводимости

Сейчас записывается `system_prompt: null`; также отсутствуют его hash,
sampling-параметры, SHA `easycad_geom`, digest worker image и policy
retry/concurrency, обязательные по bench-SPEC.

Файл: `bench/src/bench/run.py`.

### P2 — отсутствует `deviation_after_align`

При нарушении координатного контракта нельзя диагностировать, верна ли форма,
но сдвинута, или форма действительно неверна. Спека требует сохранять эту
диагностическую метрику.

Файлы: `easycad_geom/mesh.py`, `bench/src/bench/grade.py`.

## Проверки

- `git diff --check`
- `PYTHONPATH=bench/src:. .venv-poc/bin/python -m pytest bench/tests -q` — 21 passed
- `PYTHONPATH=bench/src:. .venv-poc/bin/python -m bench schema`
- `PYTHONPATH=bench/src:. .venv-poc/bin/python -m bench spec --check`
- reference pipeline: 1/1 сценарий прошёл

---

## Resolution (2026-07-25)

**Standards**
- **max-cost / искажение частичного прогона** — исправлено искажение (главная
  опасность): незапущенные сценарии теперь помечаются `not_run`, не выпадают из
  знаменателя молча, статус `partial` и предупреждение печатаются. Сам долларовый
  потолок неисполним против публичного API (он не возвращает стоимость запроса —
  проверено: `step.to_public()` не содержит cost); теперь об этом печатается явное
  предупреждение вместо иллюзии работающего кэпа. `grade.py`, `run.py`.
- **коллизия ID** — исправлено: ID содержит секунды + случайный токен,
  `mkdir(exist_ok=False)`. `run.py`.
- **CI не требует приёмки** — исправлено: `bench spec --check` падает, если
  `reference.py` изменён после записи в `validation.json` (инвариант 9). Отсутствие
  `validation.json` не является ошибкой `--check` (это задача gate в `bench run` →
  `skipped_unvalidated`). `spec.py`.
- **`--seed`** — исправлено: seed из манифеста прокидывается в surface sampling
  (`grade.py`). Он остаётся seed'ом сэмплирования (§6.3), а не генерации.

**Spec**
- **SHA-256 артефактов** — частично: публичный API отдаёт байты файла без
  эталонного хеша, сверять не с чем. Теперь пишется integrity-sha256 скачанных
  STEP/STL в `gen.json`; ограничение задокументировано. `run.py`.
- **manifest** — частично: добавлены `git_sha_easycad_geom` (тот же репозиторий),
  `concurrency`, `retry_policy`, `sampling.temperature` и `worker_image_digest`
  как явные null с пояснением. `system_prompt` недоступен через публичный API —
  оставлен null с нотой. `run.py`.
- **`deviation_after_align`** — добавлено (диагностика, §3.1): считается при
  наличии обоих мешей, пишется как `diagnostic`-чек, на вердикт не влияет.
  `easycad_geom/compare.py`, `grade.py`.

Тесты: 22 passed (добавлен тест диагностики выравнивания). `bench spec --check`
зелёный.

---

## Resolution — round 2 (2026-07-25)

**Standards**
- **session start роняет прогон до manifest** (P1) — исправлено: сбой
  `start_session` (например `/api/session/reset` при недоступном сервере) теперь
  записывается как `generation_error` этого сценария, manifest пишется в `finally`
  всегда. Проверено против выключенного сервера: 0% (n=1), primary=generation_error,
  manifest на месте, без трейсбека. `run.py`.
- **валидация аргументов** (P2) — исправлено: `--attempts < 1`, `--max-cost < 0`,
  `--cost-per-turn < 0` → выход с кодом 2. `run.py`.

**Spec**
- **--max-cost неисполним** (P1) — добавлен `--cost-per-turn <usd>`: backend
  проставляет оценку в `cost_usd`, и существующий потолок начинает реально
  срабатывать. Без флага (=0) печатается честное предупреждение, что кэп выключен
  (API не отдаёт стоимость). `backend.py`, `run.py`, `cli.py`.
- **SHA-256 артефактов не сверяется** (P1) — не исправимо в харнессе: реальный
  публичный API отдаёт байты файла БЕЗ эталонного хеша (проверено: ни `/api/chat`,
  ни `/api/export/{id}/step` не возвращают sha). Сверять не с чем. Пишем
  integrity-sha скачанного. Настоящее решение — добавить sha/ETag в ответ экспорта
  приложения; это изменение продукта, вне харнесса — по запросу.
- **manifest system_prompt=null** (P1) — через публичный API недоступно; врать,
  читая локальный исходник при удалённом сервере, нельзя (§6.3). `git_sha_app`
  фиксирует промпт для self-hosted сборки. Добавлены опциональные
  `--system-prompt-file` и `--temperature`: оператор, знающий свою конфигурацию,
  записывает её явно. `run.py`, `cli.py`.
- **reference backend исполняет невалидированные эталоны** (P1) — по проекту:
  reference backend — это self-test плумбинга (gen==ref, всегда ~100%, помечен
  «NOT a product measurement»), он структурно не производит цифру качества, к
  которой относится инвариант 9. Gate остаётся у product-прогона. Реальный
  пункт — эталоны M0 ждут ручной валидации человеком.
- **CI пропускает отсутствие validation.json** (P1) — по §4.3 это задача gate в
  `bench run` (`skipped_unvalidated`), а не `--check`. Для релизного гейта добавлен
  `bench spec --check --require-validation`, падающий на любом complete-сценарии
  без свежей приёмки. PR-гейт (по умолчанию) остаётся зелёным. `spec.py`, `cli.py`.

---

## Resolution — round 3 (2026-07-26)

**Standards**
- **malformed /api/chat роняет прогон** (P1) — исправлено: `send()` завёрнут в
  один защитный конверт — HTTP-статус, мёртвый сокет, не-JSON тело, не-dict,
  битый base64, sha-mismatch превращаются в `generation_error`, не в исключение;
  плюс safety-net вокруг `session.send()` в `_run_attempt`. 7 тестов
  (`test_backend.py`). `backend.py`, `run.py`.
- **mode=product для reference** (P1) — исправлено: `--mode` по умолчанию None,
  выводится из backend (`reference`→`selftest`, `product`→`product`).
  `cli.py`, `run.py`.

**Spec**
- **--max-cost превышается за попытку** (P1) — исправлено: введён `Budget` с
  резервированием ПЕРЕД каждым ходом, а не раз на попытку; многоходовая попытка
  больше не переливает потолок. Тест `test_budget_reserves_per_turn`. `run.py`.
- **целостность артефактов** (P1) — реализовано на стороне продукта: эндпоинт
  `/api/export/{id}/step` отдаёт заголовок `X-Content-SHA256`; backend скачивает
  и сверяет, при несовпадении — ошибка хода (не молчаливое измерение битого/
  устаревшего STEP). Проверено end-to-end (TestClient) и юнит-тестами.
  `app/main.py`, `backend.py`.
- **prompt без SHA в manifest** (P1) — исправлено: `--system-prompt-file` теперь
  пишет и полный текст, и `system_prompt_sha256` (§6.3). Значения по умолчанию
  (prompt/sampling/worker digest) остаются null — недоступны через публичный API;
  оператор задаёт их флагами. `run.py`.
- **--require-validation не подключён к гейту** (P2) — подключено к `make release`:
  релиз требует и синхронности эталонов, и ручной приёмки (§4.3). Следствие:
  эталоны M0 нужно валидировать человеком до следующего релиза. `Makefile`.

Тесты: 29 passed (добавлен `test_backend.py`: robustness + sha + budget). App-
suite по export/step зелёный. `bench spec --check` зелёный.

---

## Resolution — round 4 (2026-07-26)

**Standards**
- **сбой скачивания STEP возвращал cost_usd=0** (P1) — исправлено: 200 от
  /api/chat = вызов оплачен, поэтому оценка списывается на ВСЕХ пост-200 путях
  (success, exec-fail, artifact-fail, parse-fail). Только до-чатовые transport-
  ошибки дают 0. Тест `test_artifact_failure_still_charges_cost`. `backend.py`.
- **STEP без X-Content-SHA256 принимался** (P1) — исправлено: fail closed —
  отсутствие заголовка теперь ошибка артефакта; принять можно только явным
  `--allow-unverified-artifacts` (старый сервер). Тесты
  `test_missing_sha_header_*`. `backend.py`, `cli.py`, `run.py`.

**Spec**
- **hard --max-cost без cost signal** (P1) — реализован реальный потолок: product-
  прогон с конечным `--max-cost` без `--cost-per-turn` теперь ОТКЛОНЯЕТСЯ (rc 2);
  явный отказ от потолка — `--max-cost 0` (uncapped). `cap <= 0` = без потолка.
  Тесты `test_product_run_refuses_cap_without_estimate`, `test_budget_uncapped_*`.
  `run.py`.
- **CI без release-gate валидации** (P2) — добавлен tag-only шаг в GitHub CI:
  `bench spec --check --require-validation` на `refs/tags/v*`; PR-гейт не трогаем
  (приёмка человеком — релизная забота, §12). `.github/workflows/ci.yml`.

**Отклонено с обоснованием:**
- **SHA для inline STL** (P1) — не требуется: STL приходит инлайн base64 в теле
  JSON /api/chat. Обрезанное/битое тело → JSONDecodeError → уже фиксируется как
  ошибка. В отличие от ОТДЕЛЬНОГО скачивания STEP, инлайн-данные нельзя молча
  измерить битыми — атомарность разбора JSON уже это гарантирует. Добавлять
  второй хеш к тому же телу — избыточно; порождается угроза, которой нет. STEP
  получает хеш именно потому, что это отдельный запрос.
- **гарантия воспроизводимости manifest** (P1) — через публичный API невозможна:
  system prompt / sampling / worker digest сервер не отдаёт. Добавлено громкое
  предупреждение при product-прогоне без `--system-prompt-file`; оператор
  фиксирует их флагами, `git_sha_app` — якорь. Настоящая «гарантия» требует
  metadata-эндпоинта в приложении (решение продукта, вне харнесса) — по запросу.

Тесты: 34 passed. `bench spec --check` зелёный.

---

## Resolution — round 5 (2026-07-26)

**Standards**
- **битый --system-prompt-file ронял прогон после mkdir** (P2) — исправлено:
  путь проверяется в блоке валидации аргументов, ДО создания директории прогона;
  плохой путь → rc 2, без осиротевшей директории. Проверено. `run.py`.

**Spec**
- **--check не сверял committed STEP/STL с их SHA** (P1, новый и верный) —
  исправлено: `--check` теперь проверяет, что `expected/turn-N.step` и `.stl` на
  диске существуют и их sha256 совпадает с записанным в `turn-N.json`.
  Подменённый/битый/удалённый эталонный STL (по которому идёт grading) больше не
  пройдёт CI. Проверено: порча STL → `--check` падает. `spec.py`.
- **--allow-unverified-artifacts не помечал прогон** (P2) — исправлено: manifest
  получает `artifact_integrity` и `product_metric_compliant=false`; при флаге
  печатается предупреждение и в run, и в сводке grade. `run.py`, `grade.py`.

**Отклонено с обоснованием (повторные пункты):**
- **SHA для inline STL** (P1) — держим позицию. STL приезжает инлайн base64 в теле
  JSON /api/chat; целостность гарантирована атомарностью разбора JSON (обрезка →
  JSONDecodeError → уже ошибка). Хеш того же тела, что прислал сервер, не ловит
  ничего сверх этого — он ловил бы только транспортную порчу ОТДЕЛЬНОГО скачивания
  (как у STEP). Единственный способ добавить смысл — качать STL отдельным запросом
  с хешем, но это строго хуже (лишний round-trip, ноль выгоды). `app/main.py:1204`
  (`/api/export/{id}` для STL) харнесс не использует — STL берётся инлайн.
- **обязательный полный manifest воспроизводимости** (P1) — через публичный API
  недостижимо (prompt/sampling/worker digest сервер не отдаёт). Есть флаги +
  громкое предупреждение + `git_sha_app` как якорь. Настоящая гарантия требует
  metadata-эндпоинта в приложении — решение продукта.

Тесты: 34 passed. `bench spec --check` зелёный (+ проверка целостности артефактов).

---

## Resolution — round 6 (2026-07-26)

**Standards**
- **нечитаемый/не-UTF-8 --system-prompt-file** (P2) — исправлено: файл читается и
  декодируется в блоке валидации ДО mkdir; ошибка → rc 2, без осиротевшей
  директории. Текст стешится и переиспользуется. `run.py`.
- **--mode принимал произвольную строку** (P2) — исправлено: `choices=[product,
  offline, regrade, remeasure, selftest]`. `cli.py`.

**Spec**
- **SHA для inline STL** (P1) — **позиция изменена, реализовано.** Прошлый довод
  (атомарность JSON) был неполон: бит-флип внутри base64-строки, сохраняющий
  валидность base64 И JSON, декодируется в ДРУГОЙ STL незаметно — а §6.1 именно
  про защиту от прокси/транспорта (в этом окружении прокси есть). Теперь сервер
  отдаёт `stl_sha256` в ответе /api/chat (`Step.to_public`), backend сверяет
  декодированный STL, fail-closed с тем же opt-out. Симметрично STEP. Тесты
  `test_stl_sha_*`. `app/store.py`, `backend.py`.
- **--check fail-open при отсутствующем sha** (P1) — исправлено: `artifacts.*.
  sha256` теперь обязателен; его отсутствие в turn-N.json — ошибка (§6.1). `spec.py`.
- **compliant при неполной воспроизводимости** (P1) — исправлено: `product_metric_
  compliant` теперь требует product-прогон + проверенные артефакты + operator-
  supplied воспроизводимость; иначе False с `compliance_notes`, объясняющими
  причину. Добавлено поле `reproducibility` (operator-supplied | git-sha-anchored).
  Голый булев больше не переобещает. `run.py`.

Тесты: 36 passed. App-suite (steps/export/persist) зелёный. `bench spec --check`
зелёный.

---

## Resolution — round 7 (2026-07-26)

**Standards**
- **malformed 200 возвращал cost_usd=0** (P1) — исправлено: chat-POST заинлайнен;
  `cost` становится оценкой в момент получения 2xx (urllib кидает HTTPError на
  не-2xx, так что достижение парсинга уже означает оплаченный 2xx). Теперь битое
  тело 200 списывает оценку — повторные malformed-200 не обходят --max-cost. Тест
  `test_malformed_200_still_charges_cost`. `backend.py`.
- **help --allow-unverified-artifacts упоминал только STEP** (P2) — исправлено:
  «STEP/STL». `cli.py`.

**Spec**
- **manifest мог быть compliant при null worker digest / неполном sampling** (P1)
  — исправлено: `product_metric_compliant` теперь требует ПОЛНЫЙ набор
  воспроизводимости — prompt + sampling.temperature + worker_image_digest (плюс
  product + verified artifacts). Добавлен флаг `--worker-image-digest`; каждое
  недостающее поле перечисляется в `compliance_notes`. Проверено: с тремя флагами
  reproducibility=operator-supplied. `run.py`, `cli.py`.

**Отклонено с обоснованием:**
- **STL должен доставляться по URL, а не inline** (P1) — держим позицию.
  Требование §6.1 — целостная доставка измеряемых артефактов; «URL» там —
  МЕХАНИЗМ, продиктованный тем, что «у бенчмарка нет локальных путей». Inline
  base64 + проверенный `stl_sha256` решает ту же задачу (байты приходят в ответе,
  целостность сверяется) и даёт на один round-trip меньше. Отдельное скачивание
  STL по URL было бы строго избыточным при уже проверяемом хеше. Эндпоинт
  `/api/export/{id}` (STL) существует, если когда-нибудь понадобится буквальная
  URL-доставка, но выгоды над inline+sha у него нет.

Тесты: 37 passed (+ app-suite). `bench spec --check` зелёный.

---

## Resolution — round 8 (2026-07-26)

Standards: замечаний нет.

**Spec**
- **compliant мог сертифицировать ложную provenance** (P1) — **важно, и это
  частичный откат round 7.** Проверено в коде: `ChatRequest` НЕ имеет поля
  temperature (значит `--temperature` никогда не отправляется), а `_resolve_llm`
  вправе переопределить provider/model (триал форсит свой), и ответ не эхоит
  фактическую модель. Значит provenance через публичный API не подтверждаема, и
  гейтить `product_metric_compliant` на операторских утверждениях (как я сделал в
  round 6/7) — это сертификация непроверяемого. Исправлено по принципу §8 (не
  сертифицируй то, что не можешь проверить): `product_metric_compliant` теперь =
  product-прогон + verified artifacts (только проверяемое). Provenance пишется в
  отдельный блок `provenance` с честными метками (`model_requested` /
  `model_confirmed=null`, `sampling_temperature_asserted` + нота «API не имеет
  temperature; харнесс её не слал»). Добавлено `compliance_scope`. `run.py`.
- **--mode позволял мислейбл product-прогона** (P1) — исправлено: `mode`
  выводится из backend (product|selftest), флаг `--mode` удалён,
  `allow_abbrev=False` чтобы `--mode` не аббревиировался тихо в `--model`. `cli.py`,
  `run.py`.
- **решение inline STL не отражено в спеке** (P1) — обновлён docs/bench-SPEC.md
  §6.1: контракт целостности обоих артефактов, разные транспорты (STEP по URL +
  header sha, STL inline base64 + `stl_sha256`), обновлён `TurnResult`.
- **сообщение --check переобещало проверку validation** (P2) — исправлено:
  формулировка зависит от `--require-validation`. `spec.py`.

Тесты: 37 passed. App-suite зелёный. `bench spec --check` зелёный.
