# Регистрация и тестирование новых source

Практическое руководство: как добавить новый коннектор-источник в Universal Harvester,
зарегистрировать его экземпляр и протестировать. В качестве сквозного примера используется
коннектор `irp_thehive` (TheHive 5.x), см. `app/sources/thehive.py`.

---

## 0. Два смысла слова «source»

В проекте `source` означает **две разные вещи** — это важно не путать:

| Уровень | Что это | Где живёт |
|---------|---------|-----------|
| **Тип коннектора** (source type) | код на Python: функция-исполнитель + запись в реестре | `app/sources/*.py` + `ENGINE_SOURCES_AND_FUNCTIONS_MAP` в `app/engine.py` |
| **Экземпляр source** (source object) | строка в БД с конкретным URL/ключом/ролями | таблица `objects`, тип `source` |

Добавление поддержки новой системы = **Часть A** (код, делает разработчик).
Подключение конкретного стенда = **Часть B** (объект в UI, делает оператор).

В DSL: `GET <имя_объекта>:<имя_функции>(параметры) AS <данные>`
— `имя_объекта` ищется в таблице `objects`, из его `json.type` берётся **тип коннектора**,
а `имя_функции` — это функция внутри этого типа.

```
GET prod-thehive:get_alerts(filter={}, limit=1000) AS alerts
     │            │
     │            └── функция типа irp_thehive
     └── объект source с именем "prod-thehive", у которого json.type = "irp_thehive"
```

---

## 1. Формат БД

`db_init()` (`app/db.py`) создаёт таблицы (DDL совместим с SQLite и PostgreSQL):

| Таблица | Поля | Назначение |
|---------|------|-----------|
| `objects` | `name, roles, version, timestamp, type, owner, json` | source/script/notifier/llm (версионируются) |
| `secrets` | `system, account, secret, comment` | секреты, `secret` зашифрован Fernet |
| `users` | `enabled, name, pass, roles, json` | пользователи, роли, ноды (в т.ч. `notify`) |
| `access_networks` | `cidr, allow, comment` | IP-whitelist |

Ключевые свойства таблицы `objects`:
- `roles` и `json` хранятся как **текст с JSON** (парсятся при чтении);
- объекты **версионируются**: правка создаёт новую версию, актуальна `MAX(version)`;
- `type` ∈ `{"script", "source", "notifier", "llm"}`.

---

## 2. Анатомия source-объекта (`json`)

Поле `json` объекта типа `source` — это словарь, который движок передаёт в функцию-коннектор
как аргумент `source_object`. Структура:

```json
{
  "type": "irp_thehive",
  "url": "https://thehive.example.ru",
  "timeout": 60,
  "verify": false,
  "max_threads": 10,
  "key": { "system": "thehive", "account": "api" }
}
```

| Поле | Обязательность | Кто использует |
|------|----------------|----------------|
| `type` | **обязательно** | движок: ключ в `ENGINE_SOURCES_AND_FUNCTIONS_MAP` |
| конфиг подключения (`url`/`host`/`port`/…) | по коннектору | сам коннектор (`source["url"]` и т.п.) |
| `key: {system, account}` | если нужна аутентификация | движок резолвит секрет (см. §4) |
| `threads_limit` (int) | опционально | `get_source_threads_pool()` (сейчас в исполнение не подключён) |

> Замечание о пуле потоков: в карте коннекторов поле документируется как `max_threads`,
> а `app/db.py:get_source_threads_pool` читает `threads_limit`. Обе ветки сейчас не влияют
> на выполнение (параллелизм закомментирован), но при включении пула договоритесь об одном имени.

---

## 3. Как движок исполняет `GET` (контракты, которые надо знать)

`engine.py:commands_executor` для каждой команды `GET`:

1. **Находит объект** по имени (`get_actual_object_by_name`) среди `('source','script')`.
2. **Проверяет роли**: разрешено, если у пользователя есть роль `fullmaster` ИЛИ
   любая роль из `roles` объекта. Иначе — отказ.
3. **Берёт тип** из `json.type` и ищет функцию: `get_source_function(type, function)`.
4. **Валидирует параметры**: каждый ключ из `required` функции должен присутствовать
   в параметрах вызова, и **тип значения должен совпадать** с типом дефолта в `required`.
5. **Резолвит секрет** (если `json.key` — dict с `system`+`account`): кладёт расшифрованное
   значение в `source_object["json"]["key"]["value"]`.
6. **Считает зависимости** (`get_command_dependency`) и выполняет по стадиям, вызывая:
   ```python
   function_object(parameters, source_object_json, data_map, current_state)
   ```

---

## 4. Часть A — регистрация нового типа коннектора (код)

### 4.1 Контракт функции-исполнителя

Сигнатура **строго фиксирована** (движок вызывает позиционно):

```python
def execute_<name>(parameters, source_object, data_map, current_state):
    ...
    return success_bool, message_str, func_name_str, payload
```

| Аргумент | Что это |
|----------|---------|
| `parameters` | словарь параметров вызова из DSL (после инъекций переменных) |
| `source_object` | `json` объекта source (url/ключ/конфиг) |
| `data_map` | результаты предыдущих шагов: `{ "data_name": list_of_dicts, ... }` |
| `current_state` | сессия/конфиг (для логирования и доступа к БД/секретам) |

**Возврат — всегда кортеж из 4 элементов** (общая конвенция проекта):
`(bool успех, str сообщение, str имя_функции, payload)`.
Для источника **payload при успехе — это `list[dict]`** (философия Harvester:
«запрос → list of dicts»). При ошибке — `[]`.

### 4.2 Ленивые импорты

Все сторонние библиотеки импортируются **внутри функции**, а не на верхнем уровне модуля
(чтобы приложение стартовало без установки пакетов всех коннекторов). Стандартная библиотека
и `from app...` — можно на верхнем уровне.

```python
def execute_<name>(parameters, source_object, data_map, current_state):
    import requests          # ← лениво, внутри функции
    ...
```

### 4.3 Шаблон коннектора

```python
import syslog
from app.logging import get_log_message, logger_log, currentFuncName

def execute_<name>(parameters, source_object, data_map, current_state):
    source = source_object        # удобные алиасы (как в остальных коннекторах)
    query = parameters
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))

        token   = source["key"]["value"]                       # секрет, резолвит движок
        url     = source["url"].rstrip("/")
        timeout = source["timeout"] if "timeout" in source else 60
        verify  = source["verify"]  if "verify"  in source else False

        target  = query["some_required_param"]                 # параметр вызова

        response = requests.post(f"{url}/api/...", json={...},
                                 headers={"Authorization": f"Bearer {token}"},
                                 timeout=timeout, verify=verify)

        if response.status_code != 200:
            error_message = f"fail: response code {response.status_code} ({response.text[:512]})"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []

        data = response.json()
        if isinstance(data, list) == False:
            return False, "fail: payload is not a list", currentFuncName(), []

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(data)} rows", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), data        # ← list[dict]

    except Exception as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
```

### 4.4 Регистрация в реестре

В `app/engine.py`:

1. Импортируйте функцию (рядом с остальными `from app.sources...`):
   ```python
   from app.sources.<module> import execute_<name>
   ```
2. Добавьте запись в `ENGINE_SOURCES_AND_FUNCTIONS_MAP`:
   ```python
   "<source_type>": {
       "functions": {
           "<function_name>": {
               "required": {            # ОБЯЗАТЕЛЬНЫЕ параметры вызова + их ТИПЫ-образцы
                   "some_required_param": "",     # str
                   "limit": 1000                  # int
               },
               "unrequired": {          # документация необязательных (НЕ валидируется)
                   "verify": False
               },
               "functions": {
                   "query": execute_<name>        # ← сюда указатель на функцию
               }
           }
       },
       "required": {                    # образец конфигурации source-объекта (для документации)
           "url": "https://example.ru",
           "timeout": 60,
           #"key": {"system": "foo", "account": "bar"},
           "max_threads": 10
       },
       "unrequired": { "verify": False }
   },
   ```

> **Типы в `required` важны.** Движок сверяет `type(required[param])` с `type(значения из DSL)`.
> Если параметр — словарь, ставьте образцом `{}`; число — `0`/`1000`; строку — `""`; список — `[]`;
> булево — `False`. Иначе валидация отвергнет вызов с «wrong parameter type».

### 4.5 Когда нужен хук зависимостей

Если ваш источник **читает данные предыдущих шагов** из `data_map` (как `pandas_im`,
`duckdb_im`, `sqlite3_im`, `ollama`), добавьте разбор зависимостей в
`app/engine.py:get_command_dependency`, чтобы движок выполнил шаги в правильном порядке.
Для источников, которые ходят во внешнюю систему и не зависят от других данных
(как TheHive/iris/elastic), **ничего добавлять не нужно** — зависимостей нет.

### 4.6 Чек-лист разработчика

- [ ] Файл `app/sources/<module>.py`, функция `execute_<name>` с правильной сигнатурой.
- [ ] Возврат `(bool, str, str, list[dict])`; при ошибке payload = `[]`.
- [ ] Сторонние импорты — внутри функции.
- [ ] Логирование через `logger_log/get_log_message/currentFuncName`.
- [ ] Секрет берётся из `source["key"]["value"]` (если нужна авторизация).
- [ ] Импорт + запись в `ENGINE_SOURCES_AND_FUNCTIONS_MAP` (типы в `required` корректны).
- [ ] При зависимости от `data_map` — добавлен разбор в `get_command_dependency`.

---

## 5. Часть B — регистрация экземпляра source (объект в БД)

Через веб-интерфейс (меню **Secrets** и **Objects**; требуются роли
`secrets_admin`/`objects_admin` или `fullmaster`).

### Шаг 1. Секрет (если нужна авторизация)
Раздел **Secrets → Edit/create**:
- `System` = `thehive`, `Account` = `api`, `Secret` = API-токен, `Comment` — описание.
- Секрет шифруется Fernet и хранится в таблице `secrets`.

### Шаг 2. Объект source
Раздел **Objects → Object creator**:
- **Name**: произвольное имя экземпляра, напр. `prod-thehive`
  (правила имени: `^[0-9a-zA-Z._\]\[\s/-]{3,}$`).
- **Type**: `source`.
- **Roles**: непустой JSON-список, напр. `["soc", "default"]`
  (пользователь увидит источник, если у него есть одна из этих ролей или `fullmaster`).
- **JSON** (тело `json`): конфиг с `type` = именем коннектора из §4.4:
  ```json
  {
    "type": "irp_thehive",
    "url": "https://thehive.example.ru",
    "timeout": 60,
    "verify": false,
    "max_threads": 10,
    "key": { "system": "thehive", "account": "api" }
  }
  ```

После сохранения объект доступен в скриптах по своему **имени**.

---

## 6. Тестирование

### 6.1 Офлайн (без запуска приложения и без сети)

```bash
# 1) синтаксис
python3 -m py_compile app/sources/<module>.py app/engine.py

# 2) реестр загружается и стартовая цепочка не тянет лишнего
python3 -c "import app.engine; print('engine import OK')"

# 3) тип/функция зарегистрированы и резолвятся
python3 -c "
from app.engine import ENGINE_SOURCES_AND_FUNCTIONS_MAP as M, get_source_function
st='irp_thehive'
print('type registered:', st in M, '| functions:', list(M[st]['functions']))
ok = get_source_function(st, 'get_alerts', {'app_name':'t','app_version':'0','username':'sys'})
print('lookup ok:', ok[0], '| required:', list(ok[3][0].keys()), '| callable:', callable(ok[3][1]))
"
```

### 6.2 Живой прогон через Harvester

Меню **Harvester → Scripts**, введите скрипт и нажмите **Execute**.

Простые параметры (без запятых внутри значения) — инлайн:
```
GET prod-thehive:get_alerts(filter={}, limit=1000) AS alerts
```

Сложные структуры (словари/списки **с запятыми**) задавайте через `DEF` + инъекцию,
т.к. парсер DSL режет параметры вызова по запятым:
```
DEF {"_and":[{"_field":"status","_value":"New"},{"_field":"severity","_value":3}]} AS f
| GET prod-thehive:get_alerts(filter=%(f)d, limit=500) AS alerts
```

**Типы инъекций** в `%(имя)X`: `s` строка, `i` int, `f` float, `b` bool,
`l` список, `d` словарь, `x` «как есть» без кавычек.

Связка с in-memory SQL (джойн/фильтрация поверх полученных данных):
```
DEF {} AS f
| GET prod-thehive:get_alerts(filter=%(f)d, limit=1000) AS alerts
| GET duckdb_im:query(type="table", queries=["SELECT title, severity FROM alerts WHERE severity >= 2"]) AS high
```

### 6.3 Чтение результата и ошибок
- Успех/ошибка показываются через `ui.notify`; данные/переменные — во вкладке **Data/Variables**.
- Подробности — в логах (`syslog` + stdout, JSON-строки). Каждая ошибка пишется с именем функции:
  ищите `"function_name"` и `"message"`.

### 6.4 Типичные грабли
| Симптом | Причина | Решение |
|---------|---------|---------|
| `wrong parameter type for ...` | тип значения в DSL ≠ типу образца в `required` | поправьте образец в `required` или значение в DSL |
| `there is not parameter X for function ...` | обязательный параметр не передан | добавьте `X=...` в вызов |
| dict/list «разваливается» | запятые в инлайн-параметре режутся парсером | передавайте через `DEF ... AS v` + `%(v)d`/`%(v)l` |
| `... is not allow for user ...` | роли объекта не пересекаются с ролями пользователя | добавьте роль в объект или пользователю |
| `KeyError: 'value'` в коннекторе | нет секрета или `key` без `system`/`account` | заведите секрет и корректный `json.key` |
| источник «не найден» | имя в DSL ≠ имени объекта, либо `json.type` ≠ ключу реестра | сверьте имя объекта и `type` |

---

## 7. Краткий чек-лист «новый source с нуля»

1. **Код**: `app/sources/<module>.py` → `execute_<name>(...) -> (bool,str,str,list[dict])`, ленивые импорты.
2. **Реестр**: импорт + запись в `ENGINE_SOURCES_AND_FUNCTIONS_MAP` (верные типы в `required`).
3. **(если надо)** хук в `get_command_dependency`.
4. **Офлайн-проверка**: `py_compile` + `import app.engine` + `get_source_function`.
5. **Секрет** (Secrets) и **объект** (Objects, type=`source`, roles, json с `type` и `key`).
6. **Живой тест**: скрипт `GET <объект>:<функция>(...)` в Harvester, проверка данных и логов.
