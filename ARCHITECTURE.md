# Neon Genesis Universal Harvester (UH2S) — текущее состояние и архитектура

> Документ описывает фактическое состояние кода и поддерживается в актуальном виде по мере развития
> (последняя сверка — релиз `v0.11.3`). История версий — в [`ROADMAP.md`](ROADMAP.md).

## 1. Назначение проекта

Веб-приложение — «конструктор» для сбора и обработки данных из разнородных источников
через простой скриптовый интерфейс. Идея близка к «MCP для человека или агента»:
пользователь (или, в перспективе, LLM-агент) пишет короткий скрипт на собственном DSL,
а движок выполняет шаги по разным коннекторам (Elastic, SQL, NetBox, GitLab, YouTrack,
Ollama/llama.cpp и т.д.), связывает их результаты в единый граф зависимостей,
агрегирует через pandas и при необходимости отправляет уведомления.

Ключевые сущности предметной области:
- **source** — коннектор к внешней системе (как именно ходить за данными);
- **script** — сохранённый скрипт-сценарий на DSL;
- **notifier** — канал уведомлений (Mattermost, Telegram);
- **llm** — описание языковой модели (раздел в работе);
- **secret** — зашифрованный секрет (токен/пароль), привязанный к `system:account`.

## 2. Технологический стек

| Слой | Технология |
|------|-----------|
| Web/UI | [NiceGUI](https://nicegui.io/) поверх FastAPI/Starlette/Uvicorn |
| Редактор кода / таблицы | CodeMirror, AG Grid, Mermaid (всё через NiceGUI) |
| Обработка данных | pandas, numpy, duckdb |
| БД приложения | SQLite **или** PostgreSQL (выбирается конфигом) |
| Аутентификация | локальная (bcrypt) + опционально Keycloak (OIDC) |
| Шифрование конфигов/секретов | `cryptography.fernet` (симметричный master key) |
| Логирование | `syslog` + stdout (JSON-строки) |
| Коннекторы | elasticsearch, opensearch-py, psycopg2, pymssql, mysql, dnspython, pynetbox, requests (в т.ч. LLM-анализ через объекты llm), pexpect/pyotp (teleport) |

Управление зависимостями: добавлен `requirements.txt` (восстановлен из `import`-ов,
разбит на «ядро» и опциональные пакеты коннекторов). Пока **отсутствуют** `pyproject.toml`,
`Dockerfile`, `README`.

**Ленивая загрузка зависимостей.** Все сторонние пакеты (pandas, duckdb, psycopg2,
elasticsearch, requests и т.д.) импортируются *внутри функций*, которые их
используют, а не на верхнем уровне модулей. Поэтому приложение стартует с минимальным
набором (`nicegui`, `cryptography`, `bcrypt`), а пакет конкретного коннектора нужен только
в момент реального обращения к источнику. На верхнем уровне остаются лишь стандартная
библиотека, локальные `from app...`-импорты и веб-рантайм (`nicegui`/`fastapi`).

## 3. Структура репозитория

```
UH2S/
├── front.py                      # точка входа: CLI-аргументы, маршруты NiceGUI, middleware, запуск
├── engine.py                     # ОРКЕСТРАТОР выполнения распарсенного скрипта (commands_executor)
├── base64_json_object_creator.ipynb  # утилита: генерация зашифрованных конфигов запуска
├── .gitignore
└── app/
    ├── engine.py                 # ПАРСЕР DSL + реестр источников/функций + инъекции + run_command
    ├── interface.py              # вся UI-логика NiceGUI (login_page, main_page, draw_*)
    ├── db.py                     # доступ к БД: объекты, секреты, пользователи, сети
    ├── login.py                  # локальная аутентификация (bcrypt)
    ├── validation.py             # regex-валидация имён/паролей, проверка статуса пользователя, IP-whitelist
    ├── crptgrphy.py              # encrypt/decrypt через Fernet
    ├── logging.py                # обёртка над syslog, формат лог-сообщения
    ├── notify.py                 # отправка уведомлений (Mattermost, Telegram)
    ├── llm.py                    # каркас LLM-пайплайна (заглушки)
    ├── parse_errors.py           # разбор ошибок (вспомогательное)
    └── sources/                  # коннекторы к источникам данных (по файлу на систему)
        ├── elastic.py / elastic_requests.py / opensearch.py / manticoresearch.py
        ├── postgresql.py / mysql.py / mssql.py / sqlite3.py / duckdb.py
        ├── netbox.py / dns.py / gitlab.py / youtrack.py / iris.py / grafana.py / teleport.py
        ├── pandas.py             # in-memory агрегации/преобразования
        ├── llm_source.py         # объект llm как источник: line_analysis / data_analysis
        ├── universal_harvester.py # запуск вложенного сценария (рекурсия)
        ├── requests.py           # заготовка
        └── additional/
            ├── elastic2python.py # конвертер Elastic-ответов
            └── flatten.py        # уплощение вложенных структур
```

## 4. Поток выполнения (runtime)

```
front.py:main()
  ├─ парсит CLI-аргументы (зашифрованные db_conf и storage_key, SSL, host/port, keycloak)
  ├─ decrypt(NICEGUI_STORAGE_KEY)          # crptgrphy.py
  ├─ db_init(current_state)                # db.py — создаёт таблицы, сидит дефолтного юзера
  ├─ инициализирует KeycloakOpenID (опц.)
  ├─ AuthMiddleware                        # редирект на /login для неаутентифицированных
  └─ регистрирует страницы NiceGUI:
        /login            -> interface.login_page()
        /login/callback   -> обмен OIDC code на токены
        /                 -> interface.main_page()
        /api/...          -> ЗАКОММЕНТИРОВАН (API-запуск сценариев через curl)

interface.main_page()
  └─ меню: Settings | Secrets | Objects | AI | Harvester | Logout
        Harvester -> draw_harvester(): редактор скрипта -> кнопка Execute

draw_harvester() Execute:
  ├─ command_parser(text)          # app/engine.py — текст -> list команд (dict)
  └─ commands_executor(commands)   # engine.py (корневой) — выполнение
```

### Конвейер `commands_executor` (корневой `engine.py`)

1. **DEF/CALC** — собирает переменные; `CALC(X, Y, op[, optional]) AS Z` вычисляет новую переменную (математика/текст/datetime).
2. **Инъекция переменных** — `process_injections` подставляет переменные в параметры команд.
3. **GET (резолв)** — для каждой команды `GET`:
   - получает объект `source`/`script` из БД по имени (`get_actual_object_by_name`);
   - **проверка ролей**: роль пользователя должна быть в `roles` объекта (или `fullmaster`);
   - определяет тип источника и функцию из `ENGINE_SOURCES_AND_FUNCTIONS_MAP`;
   - валидирует наличие и типы параметров;
   - подгружает секреты (`get_secret`) в `source.json.key.value`.
4. **Зависимости** — `get_command_dependency` строит граф зависимостей: для SQL-источников
   парсит `FROM/JOIN`, для pandas берёт `target_data`, для `llm` — параметр `data`, для `APPLY` — имя входных данных.
5. **Поэтапное выполнение** — цикл по «стадиям»: на каждой выполняются команды, чьи зависимости
   уже готовы, до тех пор пока есть что выполнять. Сейчас выполнение **последовательное**
   (блок с `multiprocessing.Pool` закомментирован).
6. **NOTIFY** — выполняется в конце: резолвит notifier-объект, проверяет права и конфиг
   уведомлений пользователя, вызывает функцию-отправитель.
7. **SAVE/SHOW/PRINT** — обрабатываются интерфейсом после выполнения (вывод/скачивание), движком не исполняются.

### Модель данных в рантайме

Сквозь все функции передаётся словарь **`current_state`** (сессия + конфиг: имя/версия приложения,
master key, зашифрованный `db_conf`, имя пользователя и роли, IP, темы UI, ссылки, keycloak-флаги).
Почти каждая функция возвращает кортеж-конвенцию:

```python
(success: bool, message: str, func_name: str, payload)
```

## 5. Скриптовый DSL

Команды разделяются символом `|`, комментарии — `/* ... */`. Распознанные команды:

| Команда | Назначение | Статус |
|---------|-----------|--------|
| `DEF <value> AS <name>` | объявить переменную (ввод параметров) | работает |
| `CALC(X, Y, op[, optional]) AS Z` | вычисления над переменными: математика (PLUS/MINUS/MULT/DEV/POW), текст (TRIM/SPLIT/CONCAT/RE_SEARCH/RE_SUBSTRING), datetime (DATETIME_FORMAT/UNIXTIME_TO_DATETIME/DATETIME_TO_UNIXTIME) | работает |
| `GET source:func(params) AS data` | получить данные из источника | работает |
| `GET script:script_name(params) AS data` | выполнить сохранённый SCRIPT-объект (`script` — зарезервированное слово; объект `{"script","return"}`); параметры перекрывают его `DEF`, наружу отдаётся `return`; рекурсия с защитой от циклов/глубины. Сверка DEF↔параметры: лишний параметр без `DEF` → **error**; `DEF` без параметра (захардкожено) → **warning** | работает |
| `GET APPLY:<data>(col AS x):[unique] source:func(...) AS d` | построчное применение функции/скрипта к данным (fan-out + дедуп); fan-out **параллельный** (см. §4) | работает |
| `GET LOAD(key[, ttl_ignore]):refresh:<ttl>\|:not_refresh source:func(...) AS d` | read-through кэш поверх источника (см. `storage`) | работает |
| `LOAD(key[, ttl_ignore]) AS data` | чтение из persistent-кэша `storage` (TTL) | работает |
| `NOTIFY notifier("текст")` | отправить уведомление | работает |
| `SAVE(table\|[t...], format) [AS file]` | скачивание данных (xlsx/csv_in_zip/json_in_zip), в т.ч. групповое | работает |
| `SAVE(data, storage[, ttl]) AS key` | сохранить таблицу в persistent-кэш `storage` (общий, TTL) | работает |
| `PRINT(name\|"text")` | текст / MD-таблица в UI | работает |
| `SHOW(table, table\|matplotlib, {...})` | интерактивная таблица (aggrid) или график (2D/3D, стили, слои) | работает |

### Подстановка переменных (`process_injections`)

Собственный механизм инъекций (замена встроенного `%()s`), позволяющий вставлять типизированные
значения в JSON-параметры с указанием типа суффиксом:
`%(name)s` строка · `%(name)i` int · `%(name)f` float · `%(name)b` bool ·
`%(name)l`/`%(name)d` list/dict · `%(name)x` сырая вставка без кавычек.
После инъекций результат проверяется на валидность JSON.

## 6. Хранилище (схема БД)

`db_init` создаёт таблицы (DDL совместим с SQLite и PostgreSQL):

| Таблица | Поля | Назначение |
|---------|------|-----------|
| `access_networks` | cidr, allow, comment | IP-whitelist для доступа |
| `users` | enabled, name, pass(bcrypt), roles(json), json | пользователи и роли |
| `secrets` | system, account, secret(Fernet), comment | зашифрованные секреты |
| `objects` | name, roles(json), version, timestamp, type, owner, json | версионируемые source/script/notifier/llm |
| `executions` | id, owner, timestamp, status, json | журнал запусков (script/steps/duration/status; раздел History) |
| `storage` | id, owner, execution, json | persistent-кэш DSL (`SAVE→storage`/`LOAD`/`GET LOAD`); JSON-конверт `{created_ts,updated_ts,ttl,data}`; раздел «Хранилище» |
| `api_keys` | key_hash, owner, comment, enabled, created_at, created_by, expires_at | ключи HTTP API (sha256), раздел настроек |
| `schedules` | id, name, owner, script_name, cron, enabled, last_run, last_status, created_at, created_by, json | cron-расписания запуска script-объектов (раздел «Расписания») |
| `ai_log` | timestamp, username, model, provider, *_tokens, duration_ms, ok | журнал вызовов LLM |
| `settings` | scope, key, value | пользовательские/системные настройки (тема/шрифты, session_epoch) |

**Объект `llm`** (раздел AI). `json`: `type` (`ollama`/`openai`), `url`, `model`,
`context_window` (токены — бюджет промпта агента), `request_timeout`,
`verify`, опц. `key:{system,account}`. **Точность `url`:** для `ollama` — базовый URL
(`http://host:11434`, функции добавляют `/api/...`); для `openai`-совместимых — URL **с `/v1`**
(напр. `https://foundation-models.api.cloud.ru/v1`; функции обращаются к `{url}/models`,
`{url}/chat/completions` без добавления `/v1`).

Сидируется: сеть `127.0.0.0/8` (allow) и пользователь `harvester` с ролью `fullmaster`
(bcrypt-хэш зашит в DDL).

**Версионирование объектов**: новый объект — `version=1`; правки создают новую версию
(`create_new_object_version`), актуальной считается `MAX(version)`. История доступна в UI
(Objects → версии → CodeMirror diff вручную).

### Модель ролей
- `fullmaster` — доступ ко всему;
- админ-роли разделов: `useradmin` (пользователи), `secrets_admin` (секреты), `objects_admin` (объекты),
  `apiadmin` (API-ключи), `aiadmin` (AI), `netadmin` (сети), `storage_admin` («Хранилище»),
  `schedules_admin` («Расписания»);
- произвольные роли на объектах — доступ к конкретным source/notifier при выполнении скрипта.
- Роли назначаются свободным JSON-массивом в админ-UI пользователей.

## 7. Безопасность (как устроено сейчас)

- **Конфиги запуска зашифрованы** Fernet master key; `db_conf`/`storage_key` — зашифрованные строки.
  **Не в коде**: master key вводится интерактивно (`pwinput`) или через окружение `UH2S_MASTER_KEY`;
  `db_conf`/`storage_key` — аргументы CLI или `UH2S_DB_CONF`/`UH2S_STORAGE_KEY`. См. [`DOCKER.md`](DOCKER.md).
  Чистка секретов из git-истории — [`SECURITY_SCRUB.md`](SECURITY_SCRUB.md).
- **Секреты** хранятся в БД в зашифрованном виде, расшифровываются на лету при выполнении.
  В логах секреты не печатаются (убран дамп команды с подставленным ключом).
- **Аутентификация**: bcrypt-проверка пароля + искусственная задержка 1с против перебора;
  опционально Keycloak OIDC. HTTP API — по ключу (`X-API-Key`, sha256).
- **Авторизация**: проверка ролей при доступе к разделам UI и при выполнении `GET`/`NOTIFY`.
- **Сетевой контроль**: проверка IP клиента против `access_networks` (CIDR-whitelist).
- **TLS опционален**: HTTPS, если заданы `ssl_certfile`/`ssl_keyfile`; иначе HTTP за внешним reverse proxy.

## 8. Источники данных (коннекторы)

Реестр — `ENGINE_SOURCES_AND_FUNCTIONS_MAP` в `app/engine.py`. Как добавить и протестировать
новый коннектор — см. [`ADDING_SOURCES.md`](ADDING_SOURCES.md). Заявлено:

| Источник | Функции | Состояние |
|----------|---------|-----------|
| `elastic` (client) | generic_query, aggs_query, pid_hierarchy, pid_siblings | реализован |
| `elastic_requests` | query, aggs_query, pid_hierarchy/pid_siblings, list_indices (индексы уровня ES через console-proxy), list_data_views (data views / index patterns из saved objects Kibana/OSD); `auth_type` api_key/basic_auth; совместим с Kibana и OpenSearch Dashboards (kbn-xsrf+osd-xsrf), для мультитенантного OSD — `securitytenant` | реализован |
| `opensearch` | generic_query, aggs_query | реализован |
| `manticoresearch` | sql_query | реализован |
| `postgresql` / `mysql` / `mssql` | query (prep + final) | реализован |
| `sqlite3_im` / `duckdb_im` | in-memory query над собранными данными | реализован |
| `netbox` | search (общий поиск по типам объектов), search_cidr_by_ip (наиболее специфичный префикс для IP) — NetBox REST API 4.x | реализован |
| `dns` | resolve | реализован |
| `gitlab` | get_namespace_owner, search | реализован |
| `youtrack` | search_in_project/all_projects/all_articles | реализован |
| `irp_iris` | get_all_alerts | реализован |
| `irp_thehive` | get_alerts (TheHive 5.x, query-API: listAlert+filter+page); при `flatten` — пересборка `tags`→список и `customFields`→`custom_<name>` | реализован |
| `jira_sm` | search_issues (JQL), get_issue (коллекции → *_count), get_issue_changelog, get_issue_comments, get_issue_worklogs, get_issue_attachments, get_issue_issuelinks, search_cmdb (Assets/Insight AQL/IQL), search_cmdb_freetext (Insight `/rest/insight-am/1/search`, `criteriaType=FREETEXT`) — Jira REST v2 (on-prem/DC), auth bearer/basic; issues разворачиваются в плоские поля | реализован |
| `pandas_im` | dynamic_aggr, aggr, time_grouper_aggr, shift, union | реализован |
| `llm` (объект типа llm как источник) | line_analysis (построчный анализ, добавляет столбцы), data_analysis (анализ набора → [{}]); опц. `knowledge_base`; конфиг подключения — из самого llm-объекта (ollama/openai-совместимый по HTTP) | реализован |
| `universal_harvester` | local_scenario (вложенные сценарии) | **ссылается на несуществующие модули** |
| `teleport`, `grafana`, `python_requests` | — | **закомментированы / заглушки** |

## 9. Текущее состояние и зрелость (важно)

Проект — **рабочий прототип в середине рефакторинга**. Ключевые наблюдения:

1. **Сигнатуры функций-источников — унифицированы (исправлено).** Движок
   (`app/engine.py:run_command`) вызывает коннектор как
   `func(parameters, source_object, data_map, current_state)`. Раньше часть коннекторов была
   на старой 6-арговой сигнатуре (`data_map, source, query, step, parameters, current_state`)
   и не запускалась. Теперь **все 35 функций `execute_*` приведены к единой 4-арговой
   сигнатуре**; в конвертированных функциях в начало тела добавлены алиасы
   `source = source_object` и `query = parameters` (тело не переписывалось). Старый аргумент
   `step` новым движком не передаётся — он остался задействован только в трёх отключённых/
   незавершённых коннекторах (`grafana`, `teleport`, `universal_harvester`), где выставлен
   `step = None` (см. п.2 — эти три по-прежнему функционально не готовы).

2. **`universal_harvester.py`** импортирует `app.database.scenarios` и `app.engine.scenarios`,
   которых в дереве нет (`app/engine.py` — модуль, не пакет). Это код из другой ветки развития
   (полноценная подсистема «сценариев» с асинхронным запуском и ожиданием), сюда не перенесённой.

3. **Вывод, DSL-петля и AI.** `PRINT`/`SHOW` (aggrid + matplotlib 2D/3D/стили), `CALC`, `SAVE`
   (файл и `storage`), `LOAD`/`GET LOAD` (persistent-кэш с TTL) — реализованы; вкладка **Data/Variables**
   заполняется сводкой; **экспорт графа** выполнения (SVG/PNG/Mermaid). **AI-агент** (`draw_ai` + `app/llm.py`):
   выбор `llm`-объекта (ollama/openai-совм.) с контролем контекстного окна; ReAct-агент пишет и сам
   запускает скрипт, логирует токены. Раздел **Settings** — полноценный (тема/шрифты, пользователи/роли,
   AI, сети, API-ключи).

4. **Параллельное выполнение включено:** независимые команды стадии исполняются пулом потоков
   (`--processes`), `APPLY` — параллельный fan-out; лимит одновременных обращений к источнику —
   `max_threads` в его конфиге (по-источниковые семафоры). Граф зависимостей и линеаризация storage
   по ключу сохраняются.

5. **HTTP API** (`POST /api/script`) реализован: аутентификация по ключу (`X-API-Key`, sha256),
   выполнение DSL, ответ текстом или zip с артефактами. **MCP-сервер** (`mcp_server.py`) — отдельный
   сервис со структурированными инструментами. **Планировщик** (`app/scheduler.py`) — cron-запуск
   сохранённых скриптов в web-процессе.

6. **Секреты вне кода:** `MASTER_KEY`/`db_conf`/`storage_key` — через `pwinput`/окружение/аргументы
   (не в исходниках). Версия для логов — из git-тега/файла `VERSION` (`app/version.py`).

7. **`universal_harvester:local_scenario`** по-прежнему ссылается на отсутствующие модули (не перенесён);
   `grafana`/`teleport` — отключены в реестре. Это единственные функционально не готовые коннекторы.

## 10. Технический долг и риски

### Принятые риски (осознанное решение владельца проекта)

- **SQL как ядро продукта — принято как фича.** Источники `postgresql`/`mysql`/`mssql`/
  `sqlite3_im`/`duckdb_im` по замыслу исполняют произвольный SQL, который пишет пользователь —
  это и есть назначение приложения, а не уязвимость. Аналогично, f-string-конкатенация в
  `app/db.py` (имена объектов/секретов в `get_actual_object_by_name`, `create_new_object*`,
  `update_secret*`, `create_secret`, `delete_secret` и др.) **принимается как допустимый
  остаточный риск**.
  **Обоснование и компенсирующие контроли:** экземпляры разворачиваются для строго
  ограниченного доверенного круга лиц в рамках их обязанностей; доступ ограничен ролями
  (`fullmaster`/`*_admin`/роли на объектах), сетевым whitelist (`access_networks`) и
  аутентификацией (bcrypt/Keycloak). Отступление от MUST «2.2 SQL Injection» сделано
  сознательно под эту модель угроз.
  **Что держать в уме:** контроль-плейн (таблицы `objects`/`secrets`) делит ту же БД, поэтому
  пользователь с правами на редактирование объектов потенциально может влиять на неё через
  спецсимволы в именах — для доверенного круга это приемлемо, но при расширении аудитории
  стоит вернуться к параметризации `app/db.py`.
- **[Исправлено] Секреты в коде.** `MASTER_KEY`/`db_conf`/`storage_key` больше не в исходниках —
  вводятся через `pwinput` или окружение/аргументы (см. §7). Инструкция по чистке git-истории —
  [`SECURITY_SCRUB.md`](SECURITY_SCRUB.md).
- **[Исправлено] Пролив секретов в логи.** Убран дамп команды с подставленным ключом; значения
  параметров не логируются.
- **[Средний] Открытый редирект в OIDC callback** (`/login/callback`): `redirect_uri`
  формируется из `itself_link`, валидация ответа Keycloak минимальна — стоит проверять.
- **[Средний] Валидация токена.** Keycloak: проверяется userinfo, но не валидируются явно
  `token_use`/`aud`/issuer на стороне приложения (чеклист п.1).
- **[Низкий] Логи.** Стоит проверить, что в stdout/syslog не утекают значения секретов при отладке.

### Инженерные

- `requirements.txt`, README, **Docker** (Dockerfile/compose) и **тесты** (`tests/`, ~139 офлайн) —
  есть. Остаётся: **CI** (линт + unittest), `pyproject.toml`/lock, Docker HEALTHCHECK.
- Тесты покрывают парсер, cron, APPLY-порядок, DEF-инъекцию, TheHive/CMDB-хелперы; движок/коннекторы/UI —
  частично (нужен вынос чистых функций из `interface.py` в модуль без nicegui для тестируемости).
- Дублирование: CSS/тема и блок проверки ролей копируются в каждый `draw_*`.
- `interface.py`/`app/engine.py` — крупные, требуют декомпозиции.
- Опечатки в именах (`crptgrphy.py`, `nane`, `dynamica_agg_dict`).

## 11. Сильные стороны (что хорошо как фундамент)

- Чёткая декларативная **карта источников/функций** — легко добавлять коннекторы.
- **Граф зависимостей** между шагами + `APPLY` (fan-out по строкам) — выразительный DSL.
- Версионирование объектов и разделение source/script/notifier — продуманная объектная модель.
- Единая модель `current_state`, сквозное логирование, шифрование секретов «из коробки».
- Идея «in-memory SQL/pandas поверх собранных данных» (duckdb/sqlite3/pandas) — мощный приём
  для джойнов разнородных источников.

## 12. Направления развития

**Сделано** (детали и версии — в [`ROADMAP.md`](ROADMAP.md)):
1. ✅ Стабилизация ядра: единая сигнатура коннекторов, вынос master key из кода.
2. ✅ DSL-петля: `PRINT`/`SHOW`, `CALC`, `SAVE`(файл+`storage`), `LOAD`/`GET LOAD`; DEF-инъекция.
3. ✅ Выполнение и наблюдаемость: история запусков (`executions`), **параллельный исполнитель**, планировщик (cron).
4. ✅ API/MCP-слой: `POST /api/script` (ключи), MCP-сервер со структурированными инструментами.
5. ✅ Эксплуатация: README, Docker (образ/compose), авто-версия; частично — тесты.

**Осталось:**
1. **AI-режим (Фаза 4):** довести `app/llm.py` до полноценного пайплайна «NL-запрос → сборка DSL →
   выполнение → ответ» (агент работает частично).
2. **Качество/эксплуатация:** CI (линт + unittest), `pyproject.toml`/lock, Docker HEALTHCHECK,
   вынос чистых функций из `interface.py` для тестируемости, расширение тестов движка/коннекторов.
3. **Бэклог коннекторов:** починить/перенести сценарии `universal_harvester`; вернуть `grafana`/`teleport`;
   TheHive — авто-пагинация и сущности cases/observables.
4. **Мелочи:** опечатки имён (`crptgrphy.py`, `nane`, `dynamica_agg_dict`).

---
*Документ поддерживается по мере развития проекта.*
