# Universal Harvester — язык скриптов (DSL)

Справочник по скриптовому движку: команды, функционал, правила. Скрипт пишется и запускается
в разделе **Harvester**; объекты (источники, скрипты, нотификаторы, секреты) хранятся в **Objects**/**Secrets**.

См. также: [`ARCHITECTURE.md`](ARCHITECTURE.md), [`ADDING_SOURCES.md`](ADDING_SOURCES.md), [`PARSING.md`](PARSING.md).

---

## 1. Общие правила

- **Команды разделяются `|`.** Разделение — только на верхнем уровне: `|` внутри строк и скобок
  `()[]{}` не считается разделителем (поэтому SQLite-конкатенация `a || b` не ломает скрипт).
  ```
  GET ... AS a
  | GET ... AS b
  | PRINT(a)
  ```
- **Комментарии:** `/* ... */` (многострочные).
- **Каждый шаг даёт результат с именем** через `AS <имя>`. У `GET` это «данные» (таблица — list of dict),
  у `DEF`/`CALC` — «переменная».

### Типы значений
Литералы распознаются автоматически:
| Запись | Тип |
|--------|-----|
| `"текст"` или `'текст'` | строка |
| `true` / `false` (или `True`/`False`) | булево |
| `42` | целое |
| `3.14` | дробное |
| `[1, 2, 3]` | список (валидный JSON) |
| `{"k": "v"}` | словарь (валидный JSON) |
| без кавычек/не-число | трактуется как **имя переменной** (в CALC) |

### Параметры
Параметры пишутся как `ключ=значение`, через запятую. Запятые **внутри** кавычек и скобок
сохраняются, поэтому списки/словари/SQL со запятыми можно писать прямо в вызове:
```
GET sqlite3:query(queries=["SELECT a, b, c FROM t ORDER BY a"]) AS r
```
Обязательные параметры проверяются по схеме функции и приводятся к нужному типу; необязательные —
типизируются «по возможности».

### Подстановка переменных (инъекция)
В значения параметров можно подставлять переменные через `%(имя)X`, где `X` — тип вставки:

| Суффикс | Вставляет |
|---------|-----------|
| `s` | строку (как есть) |
| `i` | целое |
| `f` | дробное |
| `b` | булево |
| `l` | список |
| `d` | словарь |
| `x` | сырую вставку без кавычек (осторожно) |

```
DEF "events-*" AS idx
| DEF {"_field":"status","_value":"New"} AS f
| GET thehive:get_alerts(filter=%(f)d, limit=1000) AS alerts
```

---

## 2. Команды

### DEF — объявить переменную
```
DEF <значение> AS <имя>
```
```
DEF 1700000000000 AS start
| DEF "1.2.3.4" AS target
| DEF [1, 2, 3] AS nums
```

### CALC — вычисления над переменными/литералами
```
CALC(X, Y, operation[, optional]) AS Z
```
`X`, `Y`, `optional` — имена переменных или литералы.

**Математика** (X, Y — число):
| operation | результат |
|-----------|-----------|
| `PLUS` | X + Y |
| `MINUS` | X − Y |
| `MULT` | X × Y |
| `DEV` | X / Y (деление; деление на 0 → ошибка) |
| `POW` | X в степени (`optional`, иначе Y) |

**Текст:**
| operation | результат |
|-----------|-----------|
| `TRIM` | `X` без пробелов (или `optional` = символы для обрезки; `Y` игнорируется) |
| `CONCAT` | `X + Y` (или `X + optional + Y`, если задан разделитель) |
| `SPLIT` | `X`, разбитая по `Y` → список (`optional` = maxsplit) |
| `RE_SEARCH` | есть ли regex `Y` в `X` → bool |
| `RE_SUBSTRING` | первое совпадение regex `Y` в `X` (`optional` = номер группы) |

**Datetime (как текст):**
| operation | результат |
|-----------|-----------|
| `DATETIME_FORMAT` | `X` из формата `Y` → формат `optional` |
| `UNIXTIME_TO_DATETIME` | `X` (unixtime int/str) → формат `Y` (`optional` = таймзона, UTC по умолчанию) |
| `DATETIME_TO_UNIXTIME` | `X` из формата `Y` → int (`optional` = таймзона) |

```
DEF 2 AS a | DEF 3 AS b | CALC(a, b, PLUS) AS c | CALC(c, 2, POW) AS sq      /* c=5, sq=25 */
CALC(raw, "%Y-%m-%d %H:%M:%S", DATETIME_FORMAT, "%d.%m.%Y") AS pretty
CALC(line, ",", SPLIT) AS parts
```

### GET (источник) — получить данные из коннектора
```
GET <source_object>:<function>(параметры) AS <данные>
```
`<source_object>` — имя объекта типа `source` из **Objects**; `<function>` — функция этого типа источника.
Возврат — list of dict.
```
GET prod-thehive:get_alerts(filter={}, limit=1000) AS alerts
GET netbox:search(target="db-01") AS found
```

### GET (скрипт) — выполнить сохранённый скрипт
```
GET script:<script_name>(параметры) AS <данные>
```
`script` — зарезервированное слово; `<script_name>` — имя объекта типа `script`.
Объект скрипта: `{"script": "<тело DSL>", "return": "<имя данных или переменной>"}`.

- Параметры вызова **перекрывают `DEF`** под-скрипта (переданное значение побеждает дефолт).
- Наружу отдаётся то, что указано в `return`.
- Поддерживается рекурсия (с защитой от циклов и ограничением глубины).

**Сверка параметров и `DEF` скрипта:**
- параметр, которому **нет** соответствующего `DEF` → **error** (с подсказкой допустимых параметров);
- `DEF` без переданного параметра (захардкожено создателем) → **warning** (шаг выполняется);
- полное соответствие → **done**.

```
GET script:enrich_host(target="1.2.3.4") AS result
```

### GET APPLY — построчное применение (fan-out)
```
GET APPLY:<данные>(<кол1> AS <x>[, <кол2> AS <y>]):[<unique>] <source:func | script:name>(... %(x)s ...) AS <данные>
```
Для **каждой строки** входных данных значения колонок подставляются в параметры, вызывается
источник **или скрипт**, результаты помечаются `applied_<x>` и склеиваются; при заданном `[unique]`
(JSON-список колонок) — дедупликация. Работает и с `source:func`, и с `script:name`.
```
GET netbox:search(target="rack-1") AS hosts
| GET APPLY:hosts(address AS ip):["dns_name"] dns:query(target=%(ip)s) AS resolved
| GET APPLY:hosts(address AS ip):[] script:enrich(target=%(ip)s) AS enriched
```

### PRINT — текст/таблица в markdown
```
PRINT("любой текст")     /* комментарий/заголовок */
PRINT(имя)               /* переменная или таблица данных -> markdown */
```
- В кавычках → markdown-текст.
- Имя данных/таблицы → markdown-таблица.
- Имя скалярной переменной → `имя = значение`.

### SHOW — интерактивный вывод табличных данных
```
SHOW(<таблица>, <тип>[, <optional_params>])
```
- `type = table` → интерактивная таблица (фильтры, сортировка по колонкам, горизонтальный скролл).
- `type = matplotlib` → график; `optional_params` — JSON: `kind` (line/bar/…), `x`, `y` (можно список),
  `title`, `figsize=[w,h]`, `dpi` (по умолчанию 150). Несколько слоёв/типов, вторая ось Y и пороговые
  линии (`layers`, `secondary_y`, `hlines`/`vlines`) — см. подробную доку [`SHOW_MATPLOTLIB.md`](SHOW_MATPLOTLIB.md).
```
SHOW(alerts, table)
SHOW(by_sev, matplotlib, {"kind":"bar","x":"severity","y":"cnt","title":"По severity","dpi":200})
```

### SAVE — скачивание файла
```
SAVE(<таблица> | [<t1>, <t2>, ...], <format>) [AS <имя_файла>]
```
- Форматы: `xlsx`, `csv_in_zip`, `json_in_zip`.
- Групповая выгрузка `[t1, t2]`: для `xlsx` — лист на таблицу; для zip — файл на таблицу.
- `AS <имя_файла>` задаёт имя скачиваемого файла (расширение добавится по формату).
```
SAVE(alerts, xlsx)
SAVE([alerts, by_sev], json_in_zip) AS soc_dump
```

### SAVE → storage — персистентный кэш (с TTL)
```
SAVE(<таблица>, storage[, <ttl_сек>]) AS <ключ>
```
Сохраняет таблицу в БД под стабильным `<ключ>` (перезапись по ключу, с новым TTL). Без `ttl` — не истекает.
Кэш **общий** для всех пользователей (ключ глобальный). Требуется `AS <ключ>` и ровно одна таблица.
```
GET irp_thehive:get_alerts(...) AS alerts
| SAVE(alerts, storage, 3600) AS alerts_cache
```

### LOAD — загрузка из storage
```
LOAD(<ключ>[, <ttl_ignore true|false>]) AS <данные>
```
- ключа нет → ошибка (прогон останавливается);
- TTL истёк, без `ttl_ignore` → данные **удаляются**, ошибка «expired and deleted»;
- TTL истёк, `ttl_ignore=true` → данные возвращаются, шаг помечается **предупреждением**;
- валидны → возвращаются.
```
LOAD(alerts_cache) AS alerts
LOAD(alerts_cache, true) AS alerts   /* использовать даже просроченные */
```

### GET LOAD — кэш поверх источника (read-through)
```
GET LOAD(<ключ>[, <ttl_flag>]):refresh:<ttl_сек> <source:func(...)> AS <данные>
GET LOAD(<ключ>[, <ttl_flag>]):not_refresh <source:func(...)> AS <данные>
```
Если в кэше есть валидные данные (или просроченные при `ttl_flag=true`) — берутся они, **источник не вызывается**.
Иначе выполняется обращение к источнику; при `:refresh:<ttl>` результат **теневой перезаписью** сохраняется
в `<ключ>` с этим TTL (только при успехе источника), при `:not_refresh` — не сохраняется.
```
GET LOAD(alerts_cache):refresh:600 irp_thehive:get_alerts(since=%(since)s) AS alerts
GET LOAD(alerts_cache, true):not_refresh irp_thehive:get_alerts(...) AS alerts
```
Порядок в одном запуске: `SAVE(k)` выше по скрипту виден `LOAD(k)`/`GET LOAD(k)` ниже (чтение-после-записи).

### NOTIFY — уведомление
```
NOTIFY <notifier_object>("текст сообщения")
```
Отправляет сообщение через объект типа `notifier` (Mattermost/Telegram). Канал/получатель берётся
из конфигурации пользователя (`notify` в его профиле). Выполняется в самом конце.

---

## 3. Модель выполнения

1. **DEF/CALC** считаются первыми (в порядке следования).
2. **Инъекция** `%(...)` подставляет переменные в параметры команд.
3. **GET-шаги** выполняются по **графу зависимостей**:
   - in-memory SQL (`sqlite3_im`/`duckdb_im`) — зависимости из `FROM/JOIN`;
   - `pandas_im` — из `target_data`; `APPLY` — от входных данных.
   Независимые шаги могут идти раньше зависимых.
4. **Ошибка любого шага прерывает прогон**; последующие шаги помечаются «отклонён».
5. **PRINT/SHOW/SAVE** отрисовываются интерфейсом **после** выполнения, **в порядке** их следования в скрипте.
6. **NOTIFY** — в последнюю очередь.

### Статусы шагов (панель «Шаги выполнения»)
| Статус | Значение |
|--------|----------|
| ⏳ pending | ожидает |
| 🔄 running | выполняется |
| ✅ done | выполнено (для GET — число строк) |
| ⚠️ warning | выполнено с замечанием (напр. незаполненные `DEF` скрипта) |
| ❌ error | ошибка (с описанием) |
| ⛔ rejected | отклонён (после ошибки выше не запускается) |
| «Валидация скрипта» | нулевой шаг: ошибки парсинга и пред-полётных проверок |

### Доступ и секреты
- У объектов `source`/`script`/`notifier` есть список **ролей**; для использования нужна роль
  `fullmaster` или совпадающая с ролью объекта.
- Источники получают секреты по ссылке `key: {system, account}` — значение подставляется в рантайме
  (хранилище **Secrets**, шифрование Fernet).

---

## 4. Связки и сквозной пример

```
/* Период за последние сутки -> алерты TheHive -> агрегат -> вывод + выгрузка */
DEF 1719100000000 AS start
| DEF 1719186400000 AS end
| DEF {"_between":{"_field":"date","_from":%(start)i,"_to":%(end)i}} AS f      /* через DEF: запятые */
| GET prod-thehive:get_alerts(filter=%(f)d, limit=100000) AS alerts
| GET duckdb_im:query(type="table", queries=["SELECT severity, COUNT(*) AS cnt FROM alerts GROUP BY severity ORDER BY severity"]) AS by_sev
| PRINT("# Алерты за период")
| PRINT(by_sev)
| SHOW(alerts, table)
| SHOW(by_sev, matplotlib, {"kind":"bar","x":"severity","y":"cnt","title":"По severity","dpi":200})
| SAVE([alerts, by_sev], xlsx) AS soc_report
```
> Сложные значения с запятыми (словарь фильтра) задаются через `DEF` и инъектируются `%(f)d`,
> т.к. внутри `DEF` запятые не разбиваются.

---

## 5. Источники и функции

Список зарегистрированных типов источников и их функций — в реестре
`ENGINE_SOURCES_AND_FUNCTIONS_MAP` (`app/engine.py`). Каждый тип задаёт `required`-параметры
(проверяются и типизируются) и `unrequired` (опциональные). Примеры:
- `irp_thehive:get_alerts(filter, limit, [sort], [extra_data], [flatten])`
- `jira_sm`: `search_issues(jql, [limit], [fields], [expand], [raw])`, `get_issue(issue_id, [expand], [raw])`, `get_issue_changelog(issue_id, [raw])`, `get_issue_comments(issue_id, [limit], [raw])`, `get_issue_worklogs(issue_id, [limit], [raw])`, `get_issue_attachments(issue_id, [raw])` (метаданные + ссылка `content`, тело не скачивается), `get_issue_issuelinks(issue_id, [raw])`, `search_cmdb(aql, [limit], [cmdb_path], [flatten])`. Заявки разворачиваются в плоские поля; коллекции (`comment`/`worklog`/`attachment`/`issuelinks`) сводятся к `*_count` (детали — отдельными функциями); `customfield_*` переименовываются в человекочитаемые имена (через `expand=names`); `raw=true` — исходный JSON
- `netbox:search(target, [object_types], [limit], [flatten])`, `netbox:search_cidr_by_ip(target, [flatten])`
- `sqlite3_im:query(queries=[...])`, `duckdb_im:query(type, queries=[...])` — SQL поверх собранных данных
- `pandas_im:aggr/dynamic_aggr/shift/union/...` — агрегации/преобразования
- `postgresql/mysql/mssql:query(...)`, `elastic*/opensearch:...`, `gitlab/youtrack/iris/dns/...`

Как добавить новый источник — см. [`ADDING_SOURCES.md`](ADDING_SOURCES.md).

---

## 6. Частые ошибки
| Симптом | Причина / решение |
|---------|-------------------|
| dict/list «разваливается» в параметре | запятые внутри — ок инлайн; но если значение строится из переменных — задайте через `DEF` + `%(v)d`/`%(v)l` |
| `wrong parameter type` | тип значения не совпал с ожидаемым; проверьте кавычки/число/список |
| `параметры без DEF: ...` (script) | в вызове скрипта параметр, которого нет среди его `DEF` (см. подсказку допустимых) |
| `... is not allow for user ...` | у объекта нет вашей роли |
| источник «не найден» | имя в `GET` ≠ имени объекта, либо `json.type` объекта ≠ типу в реестре |
| шаг ❌ прерывает остальное | это by design: ошибка шага отклоняет нижестоящие |
