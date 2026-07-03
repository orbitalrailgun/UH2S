# Universal Harvester 2 Scripted (UH2S)

Веб-приложение для сбора и обогащения данных из множества источников с собственным
скриптовым языком (DSL). На NiceGUI + FastAPI: редактор скриптов, выполнение по шагам,
вывод таблиц/графиков, анализ потока выполнения, HTTP API и MCP-сервер.

Скрипт описывает поток: получить данные из источника (`GET`), обогатить (`GET … APPLY`),
посчитать (`CALC`), вывести (`PRINT`/`SHOW`), сохранить (`SAVE`), уведомить (`NOTIFY`).

```
DEF "2026-01-01T00:00:00+0000" AS since
| GET irp_thehive:get_alerts(since=%(since)s) AS alerts
| GET sqlite3:query(queries=["SELECT severity, COUNT(*) AS cnt FROM alerts GROUP BY severity"]) AS by_sev
| PRINT(by_sev)
| SHOW(by_sev, matplotlib, {"kind":"bar","x":"severity","y":"cnt","title":"Алерты по severity"})
```

## Документация

| Документ | О чём |
|---|---|
| [HARVESTER_DSL.md](HARVESTER_DSL.md) | Язык скриптов: команды `DEF/CALC/GET/APPLY/PRINT/SHOW/SAVE/NOTIFY`, правила, примеры |
| [SHOW_MATPLOTLIB.md](SHOW_MATPLOTLIB.md) | Графики `SHOW(table, matplotlib, …)`: слои, пороговые линии, 3D, стили SciencePlots |
| [ADDING_SOURCES.md](ADDING_SOURCES.md) | Как добавить и протестировать новый коннектор-источник |
| [API.md](API.md) | HTTP API: `POST /api/script`, аутентификация по API-ключу, примеры curl |
| [MCP.md](MCP.md) | Outward-facing MCP-сервер (`mcp_server.py`) для внешних MCP-клиентов |
| [DOCKER.md](DOCKER.md) | Развёртывание в Docker: образ, compose, тома, секреты, TLS |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Архитектура, контракты коннекторов, осознанно принятые решения |
| [PARSING.md](PARSING.md) | Как устроен парсер DSL (`command_parser`) |
| [SETTINGS_PLAN.md](SETTINGS_PLAN.md) | План раздела Settings |
| [SECURITY_SCRUB.md](SECURITY_SCRUB.md) | Чистка git-истории от секретов (маскировка) — локально и на GitHub |
| [ROADMAP.md](ROADMAP.md) | План развития и журнал версий |

## Требования

- **Python 3.12+** (код использует `match` и f-строки с вложенными кавычками).
- Зависимости — в [`requirements.txt`](requirements.txt). Часть тяжёлая (`llama-cpp-python`,
  `pymssql`) и требует сборки/системных библиотек; коннекторы импортируются **лениво** — для
  тестового запуска (логин + UI) достаточно блока «ЯДРО».

## Конфигурация и секреты

Приложению нужны три значения (в код не зашиваются, см. [SECURITY_SCRUB.md](SECURITY_SCRUB.md)):

| Значение | Назначение | Как передать |
|---|---|---|
| master key | расшифровывает `db_conf` и секреты | env `UH2S_MASTER_KEY` или интерактивный ввод (pwinput) |
| db_conf | зашифрованный объект конфигурации БД (sqlite/postgresql) | env `UH2S_DB_CONF` или `--db_conf_object` |
| storage key | зашифрованный ключ хранилища сессий nicegui | env `UH2S_STORAGE_KEY` или `--nicegui_storage_key_object` |

CLI-аргумент имеет приоритет над переменной окружения. Реальные секреты не коммитятся
(`.env`, `*.pem` — в `.gitignore`).

## Быстрый запуск — Docker (рекомендуется)

Подробности и нюансы — в [DOCKER.md](DOCKER.md).

```bash
cp env.example .env          # заполните UH2S_MASTER_KEY / UH2S_DB_CONF / UH2S_STORAGE_KEY
# для sqlite укажите внутри db_conf: db_path = /data/app.db (том uh2s-data)
export APP_VERSION=$(git describe --tags --abbrev=0)
docker compose up -d --build
docker compose logs -f uh2s
```

- Веб-интерфейс: `http://<host>:8082`
- MCP-сервер: порт `8090` (см. [MCP.md](MCP.md))

## Запуск напрямую в Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # для теста хватит блока «ЯДРО» из файла

# вариант с переменными окружения:
export UH2S_MASTER_KEY=...  UH2S_DB_CONF=...  UH2S_STORAGE_KEY=...
python front.py --host 127.0.0.1 --port 8082

# либо интерактивно: не задавайте UH2S_MASTER_KEY — master key спросят при старте (pwinput)
```

Полезные аргументы `front.py`: `--host`, `--port`, `--db_conf_object`,
`--nicegui_storage_key_object`, `--ssl_certfile`, `--ssl_keyfile`, `--keycloak_url` и др.
(`python front.py --help`).

## TLS

Опционально (оба режима):

- **HTTP за обратным прокси** (по умолчанию в контейнере): TLS терминирует внешний nginx/traefik.
- **In-app HTTPS**: если рядом есть `crt.pem` и `key.pem` (или заданы `--ssl_certfile/--ssl_keyfile`),
  приложение само отдаёт HTTPS. В контейнере примонтируйте сертификаты (см. [DOCKER.md](DOCKER.md)).

## Данные и состояние

- БД: sqlite-файл (путь `db_path` внутри `db_conf`; в Docker — `/data/app.db`, том) или внешняя PostgreSQL.
- Сессии nicegui: каталог `.nicegui/` (в Docker — том на `/app/.nicegui`).

## Планировщик (cron)

Раздел «Расписания» (админам `fullmaster`/`schedules_admin`) запускает сохранённые script-объекты по cron.
Фоновый планировщик работает в web-процессе; переключатель `UH2S_SCHEDULER` (деф. `on`). Cron — локальное
время сервера, один web-инстанс (см. [DOCKER.md](DOCKER.md)).

## Тесты

Парсер DSL покрыт офлайн-тестами (без БД и сторонних пакетов):

```bash
python -m unittest discover -s tests -t .
```

## Версия

Версия в логах определяется автоматически: git-тег → файл `VERSION` → фолбэк
(см. `app/version.py`). История версий — в [ROADMAP.md](ROADMAP.md).

## Безопасность

- Секреты — только в рантайме (env/секреты/аргументы), не в коде и не в образе.
- При утечке секретов в историю git — следуйте [SECURITY_SCRUB.md](SECURITY_SCRUB.md)
  (ротация обязательна, маскировка истории — вторично).
