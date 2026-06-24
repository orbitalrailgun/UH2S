# Universal Harvester — HTTP API

Программный запуск DSL-скриптов через HTTP. Один эндпоинт: **`POST /api/script`**.
Связанные документы: [`HARVESTER_DSL.md`](HARVESTER_DSL.md), [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. Аутентификация — API-ключи

Запросы авторизуются **API-ключом**. Ключи создаются в UI: **Settings → API-ключи**
(доступно ролям `fullmaster` / `apiadmin`).

- При создании указывается **владелец** (username существующего пользователя), комментарий и, опционально,
  **срок жизни** (в днях; пусто — бессрочный). Фиксируются **время создания** и **кем создан**.
- **Токен показывается один раз** при создании — сохраните его. В БД хранится только его `sha256`-хэш.
- Запрос выполняется **в контексте владельца** ключа (его роли, права на source/notifier-объекты).
- Ключ действует, пока он **включён** (`enabled`), **не истёк** (по сроку жизни) и **владелец не заблокирован**.
- В Settings ключ можно **включить/выключить** и удалить; в списке видны статус (активен/выключен/истёк),
  время создания, автор и дата истечения.

Передача ключа в запросе (любой из вариантов):

```
X-API-Key: uh_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Authorization: Bearer uh_xxxxxxxxxxxx...
```

---

## 2. Эндпоинт

### `POST /api/script`

**Тело запроса** — текст DSL-скрипта. Поддерживаются два формата:

- `Content-Type: text/plain` — тело целиком трактуется как скрипт;
- `Content-Type: application/json` — объект `{"script": "<текст скрипта>"}`.

**Ответ зависит от содержимого скрипта:**

| Что в скрипте | Ответ |
|---------------|-------|
| Только `PRINT` (и/или `SHOW(..., table)`) | `text/plain` — собранный текст (значения, markdown-таблицы) |
| Есть `SHOW(..., matplotlib)` и/или `SAVE(...)` | `application/zip` (`result.zip`): `output.txt` + изображения `plot_*.png` + файлы из `SAVE` |

Соответствие команд артефактам:
- **`PRINT(x)`** — текст: литерал в кавычках → как есть; имя таблицы → markdown-таблица; имя переменной → `name = value`.
- **`SHOW(table, matplotlib, {...})`** — PNG-файл `plot_N_<table>.png` в zip.
- **`SHOW(table, table)`** — markdown-таблица в `output.txt`.
- **`SAVE([t1,t2], format) [AS name]`** — файл из движка (`xlsx` / `csv_in_zip` / `json_in_zip`) в zip.

**Коды ошибок:** `401` — нет/неверный/отключённый ключ или заблокирован владелец; `400` — пустое тело,
ошибка парсинга или ошибка выполнения скрипта (текст в `detail`).

---

## 3. Примеры curl

Везде ниже: `BASE` — адрес приложения, `KEY` — ваш API-ключ.

```bash
BASE="https://localhost:8080"      # при self-signed добавьте -k
KEY="uh_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### 3.1 Текстовый результат (PRINT)

```bash
curl -k -X POST "$BASE/api/script" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: text/plain" \
  --data-binary $'PRINT("Привет от API")'
```

Скрипт с таблицей (markdown в ответе):

```bash
curl -k -X POST "$BASE/api/script" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: text/plain" \
  --data-binary $'GET sqlite:query("SELECT 1 AS n, \'ok\' AS status") AS t | PRINT(t)'
```

### 3.2 JSON-обёртка тела

```bash
curl -k -X POST "$BASE/api/script" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"script": "PRINT(\"hello json\")"}'
```

### 3.3 Скрипт из файла

```bash
curl -k -X POST "$BASE/api/script" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: text/plain" \
  --data-binary @script.harvester
```

### 3.4 Изображение matplotlib (SHOW) → zip с PNG

`-OJ` сохранит файл под именем из `Content-Disposition` (`result.zip`):

```bash
curl -k -X POST "$BASE/api/script" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: text/plain" \
  --data-binary $'GET sqlite:query("SELECT 1 AS x, 10 AS y UNION SELECT 2,20 UNION SELECT 3,15") AS d | SHOW(d, matplotlib, {"x":"x","y":"y","kind":"line"})' \
  -OJ
# -> result.zip: output.txt + plot_1_d.png
unzip -l result.zip
```

### 3.5 Файлы SAVE → zip

```bash
curl -k -X POST "$BASE/api/script" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: text/plain" \
  --data-binary $'GET sqlite:query("SELECT 1 AS a UNION SELECT 2") AS rows | SAVE(rows, xlsx) AS report' \
  -OJ
# -> result.zip: report.xlsx (и output.txt, если были PRINT)
```

Несколько таблиц / форматы `csv_in_zip` | `json_in_zip`:

```bash
curl -k -X POST "$BASE/api/script" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: text/plain" \
  --data-binary $'GET sqlite:query("SELECT 1 AS a") AS t1 | GET sqlite:query("SELECT 2 AS b") AS t2 | SAVE([t1,t2], csv_in_zip) AS export' \
  -OJ
```

### 3.6 Смешанный вывод (текст + график + файл)

```bash
curl -k -X POST "$BASE/api/script" \
  -H "X-API-Key: $KEY" \
  --data-binary $'GET sqlite:query("SELECT 1 AS x,10 AS y UNION SELECT 2,20") AS d | PRINT("Отчёт") | PRINT(d) | SHOW(d, matplotlib, {"x":"x","y":"y"}) | SAVE(d, xlsx) AS data' \
  -OJ
# -> result.zip: output.txt (текст + таблица), plot_1_d.png, data.xlsx
```

### 3.7 Ошибки

```bash
# нет ключа -> 401
curl -k -i -X POST "$BASE/api/script" --data-binary 'PRINT("x")'

# ошибка парсинга -> 400 с detail
curl -k -i -X POST "$BASE/api/script" -H "X-API-Key: $KEY" --data-binary 'PRINT('
```

---

## 4. Замечания по безопасности

- Ключ даёт право выполнять **произвольные** DSL-скрипты в контексте владельца (включая SQL — это
  принятая модель приложения для доверенного круга). Выдавайте ключи владельцам с минимально
  необходимыми ролями и ограничивайте доступ сетевым whitelist при необходимости.
- Эндпоинт `/api/*` не требует cookie-сессии (свой механизм ключей), но по-прежнему обслуживается тем же
  приложением — доступ к нему ограничивается на сетевом уровне (reverse-proxy / Istio) по вашим политикам.
- Токен не восстановим: при утере — удалите ключ в Settings и создайте новый.
