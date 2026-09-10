# one-on-one automation

Українською · [English](README.md)

Автоматизує фоллоуапи після 1:1-зустрічей. Бот читає транскрипт зустрічі,
складає фоллоуап у форматі ARCV, надсилає чернетку ведучому в Telegram на
перевірку, а після підтвердження — доставляє її учаснику й заводить власні
задачі ведучого в Google Tasks.

```
Google Calendar ──> найближча 1:1 ──> нагадування за 90 хв (з минулих фоллоуапів)
       │                              ├─> ведучому: повний бриф
       │                              └─> учаснику: лише його частина
       └────────> завершена 1:1 ──> транскрипт із Gmail ──> LLM ──> чернетка фоллоуапу
                                                                      │
                                       Telegram (кнопка «Підтвердити») ┤
                                                                      ├─> фоллоуап учаснику
                                                                      └─> задачі ведучого в Google Tasks
```

Єдине сховище стану — Google Sheets, без PostgreSQL, без Make і **без Google
Apps Script**: усе робить Python-сервіс. **Фоллоуап — єдине джерело стану**:
нагадування будується з минулих фоллоуапів, окремої бази задач немає (див.
`CLAUDE.md`, Rule 0).

## Як це працює, від початку до кінця

1. **Цикл** (`run-cycle`) за розкладом дивиться календар у робочі години
   (будні, `WORK_HOURS_START`–`WORK_HOURS_END`).
2. Для **завершеної** 1:1 знаходить транскрипт у Gmail, віддає його LLM і кладе
   чернетку фоллоуапу (`summary_status=draft`) у лист `Meetings`.
3. Чернетка йде **ведучому** в Telegram з кнопкою «Підтвердити». Правка — це
   **reply** новим текстом (бот перевидає чернетку).
4. За кнопкою фоллоуап доставляється **учаснику**, а задачі, де відповідальний —
   ведучий (`HOST_NAME`), створюються як **Google Tasks** зі строком.
5. Для **майбутньої** 1:1, за 90 хвилин до неї, виходить нагадування (зібране з
   минулих фоллоуапів): **ведучий** отримує повний бриф (усе відкрите + що
   підняти), а **учасник** — лише свою частину.

Повні правила — у [`CLAUDE.md`](CLAUDE.md).

## Учасники, матчинг і підключення

Лист `Managers` — це **довідник учасників**. Один рядок на людину, з якою
проводять 1:1; колонки: `manager_id`, `manager_name`, `aliases` (через кому чи
крапку з комою), `telegram_chat_id`, `telegram_thread_id`, `calendar_id`,
`transcript_sender`, `timezone`, `active`.

**Щоб щось отримувати, учасник має запустити бота.** `telegram_chat_id` руками
ніхто не заповнює — він проставляється через самообслуговування:

1. Учасник відкриває бота в Telegram і надсилає будь-яке перше повідомлення.
2. Бот просить нік (у нашому випадку «як в ПУПі» — як у внутрішньому довіднику).
3. Бот знаходить рядок за `manager_id` / `manager_name` / `aliases` (без
   урахування регістру, `@` на початку ігнорується) і записує `telegram_chat_id`.
4. Ведучий отримує повідомлення `Підключився: <нік> ← @username`.

Два запобіжники проти підміни: рядок із **уже заповненим** `telegram_chat_id`
ніколи не перезаписується (бот відповідає «нік уже прив'язаний до іншого чату»),
а про кожне вдале підключення дізнається ведучий — тож чужий chat_id не можна
тихо підставити замість справжнього учасника, а спробу видно.

**Матчинг транскрипту до людини** — за ключовим словом у назві зустрічі
(`CALENDAR_TITLE_KEYWORDS`) плюс аліасами менеджера, пословно, тож аліас `Олена`
знайдеться у відповідальному `Олена Коваленко`.

**Що отримує кожен учасник** (лише якщо має `telegram_chat_id`, тобто
підключився):

- **фоллоуап** — після того, як ведучий його підтвердив;
- **нагадування за 90 хв** — лише свою частину (його домовленості й що на ньому
  висить), і тільки якщо щось справді висить.

## Стек

Python 3.12, FastAPI, Google API (Calendar/Gmail/Sheets/Tasks), Telegram Bot
API, Gemini (або OpenAI-сумісний провайдер). Тести — pytest, лінт — ruff.

## Вимоги

- **Проєкт Google Cloud** з увімкненими API: Calendar, Gmail, Sheets, Tasks.
- **OAuth-клієнт** (Desktop) → `secrets/google-oauth-client.json`. Скоупи:
  `calendar.readonly`, `gmail.readonly`, `spreadsheets`, `tasks`.
- **Google-таблиця** (див. нижче).
- **Telegram-бот** (токен у @BotFather).
- **Ключ Gemini** (або OpenAI). Безкоштовний тариф Gemini — 20 запитів на добу
  на модель на проєкт; `GEMINI_API_KEY` приймає список ключів через кому з
  **різних** GCP-проєктів для запасу (див. `CLAUDE.md`, Rule 5).

> **Важливо:** сервіс **одноарендний** — прив'язаний до одного Google-акаунта й
> одного ведучого (`HOST_*`), одна таблиця. Щоб ним користувався хтось інший,
> він розгортає свій екземпляр зі своїми ключами і своєю таблицею.

## Налаштування

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env      # заповнити значення
```

Кожна змінна описана в [`.env.example`](.env.example). Секрети (`.env`,
`secrets/`) у git не потрапляють (див. `.gitignore`).

### Google-таблиця

1. Створіть Google-таблицю (будь-яка назва), відкрийте й скопіюйте її ID з URL
   (`docs.google.com/spreadsheets/d/<ОЦЕ>/edit`) у `GOOGLE_SHEET_ID`.
2. Лишіть її порожньою — на першому запуску бот сам створить вкладки `Managers`
   і `Meetings` з правильними заголовками (жодного Apps Script ставити не треба;
   тека `scripts/` у репозиторії навмисно порожня).
3. Заповніть вкладку `Managers`: рядок на кожного учасника (`manager_id`,
   `manager_name`, `aliases`, а також `calendar_id`/`transcript_sender`, якщо
   відрізняються від дефолтів). `telegram_chat_id` лишіть порожнім — його
   проставить онбординг (див. вище).

### OAuth-токен

```bash
python -m app.google_oauth        # один раз відкриє браузер, створить secrets/google-token.json
```

## Локальний запуск

```bash
uvicorn app.main:app --reload     # ENABLE_SCHEDULER/ENABLE_TELEGRAM_BOT=true → цикл і long polling у процесі
```

### CLI-джоби

```bash
python -m app.cli --job cycle                          # повний цикл (транскрипти + нагадування + синк)
python -m app.cli --job transcripts                    # лише скласти чернетки з нових транскриптів
python -m app.cli --job followups                      # лише розіслати нагадування за 90 хв
python -m app.cli --job send-summary --meeting-id <id> # відправити один перевірений фоллоуап
```

Ті самі джоби доступні по HTTP: `POST /jobs/run-cycle`,
`/jobs/process-transcripts`, `/jobs/send-followups`,
`/jobs/send-summary/{meeting_id}`, а також `/telegram/webhook` і `/health`.

Опитувати бота може лише один процес (решті Telegram віддає 409), тож або
long polling, або webhook — не обидва разом.

## Деплой на Cloud Run

Контейнер масштабується до нуля, постійного процесу немає:

- `ENABLE_SCHEDULER=false`, `ENABLE_TELEGRAM_BOT=false`;
- **Cloud Scheduler** б'є в `POST /jobs/run-cycle` (заголовок
  `Authorization: Bearer <INTERNAL_JOB_TOKEN>`) за розкладом робочих годин;
- **Telegram webhook** шле апдейти на `POST /telegram/webhook` (заголовок
  `X-Telegram-Bot-Api-Secret-Token` має дорівнювати `TELEGRAM_WEBHOOK_SECRET`).

```bash
# 1. деплой
gcloud run deploy one-on-one-automation --source . --region <region> \
  --allow-unauthenticated --min-instances 0 --max-instances 1 \
  --env-vars-file env.yaml

# 2. націлити Telegram на сервіс (сам вимкне long polling)
#    setWebhook url=<service-url>/telegram/webhook secret_token=<TELEGRAM_WEBHOOK_SECRET>

# 3. створити Cloud Scheduler job на <service-url>/jobs/run-cycle
gcloud scheduler jobs create http one-on-one-cycle --location <region> \
  --schedule "*/5 8-18 * * 1-5" --time-zone "Europe/Kyiv" \
  --uri "<service-url>/jobs/run-cycle" --http-method POST \
  --headers "Authorization=Bearer <INTERNAL_JOB_TOKEN>"
```

`setWebhook` сам вимикає long polling, тож порядок міграції жорсткий: спершу
підняти новий сервіс, потім перемкнути webhook, і лише тоді гасити старий.

## Тести й лінт

```bash
python -m pytest -q
python -m ruff check app/ tests/
```
