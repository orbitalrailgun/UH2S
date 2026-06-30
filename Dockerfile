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
#  build-essential/cmake/pkg-config — сборка llama-cpp-python;
#  freetds-dev — сборка и работа pymssql (источник mssql);
#  libgomp1 — OpenMP для numpy/llama; ca-certificates/curl — TLS-доверие и healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        pkg-config \
        freetds-dev \
        libgomp1 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости (кешируемый слой). Установка всех пакетов из requirements.txt
# (включая llama-cpp-python — сборка долгая и требует памяти).
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

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

# host 0.0.0.0 — слушать вне контейнера. TLS опционален: если в /app есть crt.pem и key.pem
# (примонтированы томом) — будет HTTPS, иначе HTTP (TLS терминирует внешний reverse proxy).
CMD ["python", "front.py", "--host", "0.0.0.0", "--port", "8082"]
