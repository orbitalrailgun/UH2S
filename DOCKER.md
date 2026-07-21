# Развёртывание UH2S в Docker

## Состав

- `Dockerfile` — образ на Python 3.12 со всеми зависимостями из `requirements.txt`
  (включая `llama-cpp-python`, `pymssql` и пр. — сборка долгая, образ крупный).
- `docker-compose.yml` — два сервиса: `uh2s` (веб, порт 8082) и `mcp` (MCP-сервер, порт 8090),
  именованные тома для БД и состояния сессий.
- `.dockerignore` — что не кладём в образ (в т.ч. `.git`, секреты, сертификаты).
- `env.example` — шаблон переменных окружения.

## Секреты (инъекция в рантайме, не в образе)

Передаются окружением (приоритет у явных аргументов CLI):

| Переменная | Назначение | Раньше |
|---|---|---|
| `UH2S_MASTER_KEY` | master key для расшифровки `db_conf`/secrets | ввод `pwinput` |
| `UH2S_DB_CONF` | зашифрованный объект конфигурации БД | аргумент `--db_conf_object` |
| `UH2S_STORAGE_KEY` | зашифрованный ключ хранилища сессий nicegui | аргумент `--nicegui_storage_key_object` |
| `UH2S_EXPORT_DIR` | каталог temp-файлов экспорта SAVE (деф. системный tempdir) | — |
| `UH2S_DOWNLOAD_INLINE_MAX_MB` | порог inline-скачивания SAVE, МБ (деф. 50); также `--download_inline_max_mb` | — |

`mcp_server.py` читает `UH2S_DB_CONF`/`UH2S_MASTER_KEY` из тех же переменных.

**Скачивание больших выгрузок и TLS.** Результат `SAVE(...)` отдаётся так: файлы не больше
`UH2S_DOWNLOAD_INLINE_MAX_MB` — blob'ом через websocket (надёжно, не зависит от доверия к
TLS-сертификату сервера); больше порога — потоковым роутом `/download/{token}` (мимо websocket, чтобы
не держать гигабайты в памяти). Роут — это обычный HTTP(S)-запрос, поэтому под **самоподписанным**
сертификатом download-подсистема браузера рвёт скачивание больших файлов («проверьте подключение»).
Для больших экспортов используйте **доверенный TLS** (валидный сертификат или reverse-proxy с
терминацией TLS), либо поднимите порог `UH2S_DOWNLOAD_INLINE_MAX_MB` (ценой памяти браузера на blob).
Каталог `UH2S_EXPORT_DIR` смонтируйте на том с достаточным местом (temp-файлы удаляются после отдачи).

## Быстрый старт

```bash
cp env.example .env
# заполните UH2S_MASTER_KEY / UH2S_DB_CONF / UH2S_STORAGE_KEY в .env
# для sqlite: внутри db_conf укажите db_path = /data/app.db (том uh2s-data)

# версия в логах из git-тега:
export APP_VERSION=$(git describe --tags --abbrev=0)

docker compose up -d --build
docker compose logs -f uh2s
```

Веб-интерфейс: `http://<host>:8082` (или порт из `UH2S_PORT`).

## TLS

Опционально (оба режима):

- **HTTP за обратным прокси** (по умолчанию): контейнер слушает HTTP, TLS терминирует
  внешний nginx/traefik/ingress. Ничего дополнительно не нужно.
- **In-app TLS**: примонтируйте сертификаты — `front.py` сам включит HTTPS. В `docker-compose.yml`
  раскомментируйте:
  ```yaml
  - ./crt.pem:/app/crt.pem:ro
  - ./key.pem:/app/key.pem:ro
  ```

## Данные и состояние (тома)

- `uh2s-data` → `/data` — sqlite-БД (`db_path = /data/app.db`). Общий для `uh2s` и `mcp`.
- `uh2s-storage` → `/app/.nicegui` — состояние/сессии nicegui.

При внешней БД (PostgreSQL и т.п.) том `uh2s-data` не нужен — конфигурация в `db_conf`.

## Сборка отдельного образа (без compose)

```bash
docker build --build-arg APP_VERSION=$(git describe --tags --abbrev=0) -t uh2s:latest .

docker run -d --name uh2s -p 8082:8082 \
  -e UH2S_MASTER_KEY=... -e UH2S_DB_CONF=... -e UH2S_STORAGE_KEY=... \
  -e UH2S_SHOW=false \
  -v uh2s-data:/data -v uh2s-storage:/app/.nicegui \
  uh2s:latest
```

## Healthcheck (liveness)

- **web (`uh2s`)**: `HEALTHCHECK` в образе опрашивает `GET /healthz` (без аутентификации, БД не
  трогает — только подтверждает, что процесс принимает HTTP). Пробуется сначала HTTP, затем HTTPS
  (сертификат может быть самоподписанным). Параметры: `interval=30s`, `timeout=5s`, `start-period=40s`
  (на ленивые импорты и init БД), `retries=3`. В `docker-compose.yml` та же проверка продублирована
  явно — интервалы можно менять без пересборки образа.
- **mcp**: у streamable-http нет отдельного health-эндпоинта, поэтому compose проверяет, что процесс
  принимает соединение на `:8090/mcp` (любой HTTP-ответ = живой).
- Проверить вручную: `curl -fsS http://127.0.0.1:8082/healthz` (в контейнере или с хоста при
  проброшенном порте). Статус контейнера — `docker ps` (колонка STATUS: `healthy`/`unhealthy`) или
  `docker inspect --format '{{.State.Health.Status}}' uh2s`.

## Планировщик расписаний (cron)

Раздел «Расписания» запускает сохранённые script-объекты по cron. Фоновый планировщик работает
**только в web-сервисе** (`front.py`), в mcp — нет (двойной запуск исключён). Управляется
`UH2S_SCHEDULER` (`on`/`off`, деф. `on`). Cron — **локальное время сервера**; пропущенные при простое
срабатывания не догоняются. **Держите один web-инстанс**: при нескольких репликах без лидер-лока
расписания сработают в каждой (двойные запуски).

## Зависимости и воспроизводимость

Канонический манифест — `pyproject.toml` (ядро + группы `optional-dependencies`: sso/mcp/connectors/
llama/dev/all). Точные версии всего дерева зафиксированы в `uv.lock`. Образ ставится **воспроизводимо**
из `requirements.lock.txt` (пиннинг, сгенерированный из lock), а не из «плавающего» `requirements.txt`.

Обновление зависимостей:

```bash
# 1) поправить версии/пакеты в pyproject.toml, затем пересчитать lock:
uv lock
# 2) перегенерировать пиннинг для Docker (рантайм-экстры, без dev/ruff):
uv export --frozen --no-hashes --no-emit-project \
  --extra sso --extra mcp --extra connectors --extra llama -o requirements.lock.txt
# 3) пересобрать образ:
docker compose build
```

`requirements.txt` оставлен как быстрый/ручной путь установки и документация назначения пакетов;
при расхождении верьте `pyproject.toml`/`uv.lock`.

## Заметки

- Образ крупный из-за `llama-cpp-python` (компиляция) и драйверов БД; первая сборка длительная.
- `.git` намеренно исключён из образа (`.dockerignore`) — меньше вес и не попадают секреты из истории.
- Версия для логов берётся из файла `VERSION` (создаётся в образе из `APP_VERSION`), т.к. `.git` в образе нет.
- `UH2S_SHOW=false` отключает попытку открыть браузер внутри контейнера.
