# UH2S — Universal Harvester 2 Scripted
# Базовый образ: Python 3.12 (код использует match и f-строки с вложенными кавычками — нужен >=3.12).
FROM python:3.12-slim

# Версия для логов: в образе нет .git, поэтому app/version.py читает её из файла VERSION.
# Передавайте актуальное значение: --build-arg APP_VERSION=$(git describe --tags --abbrev=0)
ARG APP_VERSION=0.0.0-docker

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UH2S_SHOW=false

# Системные зависимости:
#  build-essential/pkg-config — сборка нативных расширений (напр. pymssql) из sdist при отсутствии wheel;
#  freetds-dev — сборка и работа pymssql (источник mssql);
#  libgomp1 — OpenMP для numpy; ca-certificates/curl — TLS-доверие и healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        freetds-dev \
        libgomp1 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости (кешируемый слой). Ставим ВОСПРОИЗВОДИМО из пиннинга requirements.lock.txt
# (сгенерирован из uv.lock: `uv export ... -o requirements.lock.txt`) — точные версии всего дерева,
# включая llama-cpp-python (сборка долгая, требует тулчейн). При правке зависимостей: обновите
# pyproject.toml -> `uv lock` -> перегенерируйте requirements.lock.txt.
COPY requirements.lock.txt ./
RUN pip install --upgrade pip && pip install -r requirements.lock.txt

# Затем код приложения.
COPY . .

# Версия для логов (нет .git в образе).
RUN echo "$APP_VERSION" > VERSION

# Непривилегированный пользователь; каталоги под состояние/данные (монтируются томами).
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.nicegui /data \
    && chown -R appuser:appuser /app /data
USER appuser

# Веб-интерфейс (front.py). MCP-сервер запускается отдельной командой (см. docker-compose.yml).
EXPOSE 8082

# Liveness: GET /healthz (без аутентификации). TLS опционален, поэтому пробуем HTTP, затем HTTPS
# (-k: сертификат может быть самоподписанным). start-period даёт время на ленивые импорты и init БД.
# Для MCP-сервиса проверка переопределяется в docker-compose.yml (другой порт/путь).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8082/healthz || curl -fsSk https://127.0.0.1:8082/healthz || exit 1

# host 0.0.0.0 — слушать вне контейнера. TLS опционален: если в /app есть crt.pem и key.pem
# (примонтированы томом) — будет HTTPS, иначе HTTP (TLS терминирует внешний reverse proxy).
CMD ["python", "front.py", "--host", "0.0.0.0", "--port", "8082"]
