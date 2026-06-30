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

`mcp_server.py` читает `UH2S_DB_CONF`/`UH2S_MASTER_KEY` из тех же переменных.

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

## Заметки

- Образ крупный из-за `llama-cpp-python` (компиляция) и драйверов БД; первая сборка длительная.
- `.git` намеренно исключён из образа (`.dockerignore`) — меньше вес и не попадают секреты из истории.
- Версия для логов берётся из файла `VERSION` (создаётся в образе из `APP_VERSION`), т.к. `.git` в образе нет.
- `UH2S_SHOW=false` отключает попытку открыть браузер внутри контейнера.
