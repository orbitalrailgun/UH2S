import re
import syslog
from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
from app.sources.additional.flatten import flatten_data
from app.sources.additional.http_proxy import proxies_from_source, proxy_kwargs
from app.sources.additional.http_retry import request_with_retry, retry_config
from app.sources.additional.cmdb import (attribute_name_map, attribute_names_from_objects,
                                         cmdb_objects_to_long, cmdb_objects_to_table,
                                         object_type_ids_with_unnamed_attributes)


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    if value is None:
        return default
    return bool(value)


def _jira_headers(source):
    """Заголовки авторизации JSM.

    auth_type=bearer (по умолчанию): Authorization: Bearer <token> — PAT (Jira DC/Server).
    auth_type=basic: Authorization: Basic base64(email:token) — Atlassian Cloud (email из source).
    Токен берётся из source['key']['value'] (хранилище секретов)."""
    token = source["key"]["value"]
    auth_type = (source.get("auth_type") or "bearer").strip().lower()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if auth_type == "basic":
        import base64
        email = source.get("email", "")
        headers["Authorization"] = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
    else:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _expand_with_names(expand):
    """Добавить 'names' к expand (карта id->имя поля для раскрытия customfield_*)."""
    parts = [p.strip() for p in (expand or "").split(",") if p.strip()]
    if "names" not in parts:
        parts.append("names")
    return ",".join(parts)


def _unfold_issue(issue, names=None):
    """Развернуть заявку Jira в плоский dict: поднять вложенный 'fields' на верхний уровень
    и уплощить вложенные объекты/списки (status -> status_name, assignee -> assignee_displayName и т.п.).

    Коллекции (comment/worklog/attachment/issuelinks) сводятся к *_count (детали — отдельными функциями).
    Если задан names (из expand=names), поля customfield_* переименовываются в человекочитаемые имена."""
    if not isinstance(issue, dict):
        return issue
    merged = {k: v for k, v in issue.items() if k not in ("fields", "names")}
    fields = issue.get("fields")
    if isinstance(fields, dict):
        fields = dict(fields)
        # коллекции-словари ({inner_key: [...], total}) -> *_count
        for field_name, inner_key in (("comment", "comments"), ("worklog", "worklogs")):
            value = fields.get(field_name)
            if isinstance(value, dict) and inner_key in value:
                fields[f"{field_name}_count"] = value.get("total", len(value.get(inner_key) or []))
                del fields[field_name]
        # коллекции-списки -> *_count
        for field_name in ("attachment", "issuelinks"):
            value = fields.get(field_name)
            if isinstance(value, list):
                fields[f"{field_name}_count"] = len(value)
                del fields[field_name]
        # человекочитаемые имена для customfield_* (по карте names из expand=names)
        if names:
            renamed = {}
            for key, value in fields.items():
                target = key
                if key.startswith("customfield_") and names.get(key):
                    target = names[key]
                    if target in renamed or target in merged:
                        target = f"{target} [{key}]"   # снятие коллизии имён
                renamed[target] = value
            fields = renamed
        merged.update(fields)
    return flatten_data(merged)


def execute_jira_search_issues(parameters, source_object, data_map, current_state):
    """Поиск заявок (issues) JSM по JQL (Jira REST API v2, POST /rest/api/2/search).

    Параметры: jql -- строка JQL; limit -- максимум заявок; fields -- (опц.) список полей;
    expand -- (опц.) строка expand; flatten -- (опц.) уплощить вложенные поля.
    Возврат: list of dict."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        jql = query["jql"]
        try:
            limit = int(query["limit"]) if query.get("limit") else 50
        except (TypeError, ValueError):
            limit = 50
        fields = query.get("fields") if isinstance(query.get("fields"), list) and query.get("fields") else None
        expand = query.get("expand") or ""
        raw_flag = _as_bool(query.get("raw", False))   # raw=true -> исходный JSON без раскрытия

        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)
        # повторы транзиентных неудач (сеть/таймаут/429/5xx) — параметры из конфига источника
        retry_kwargs = retry_config(source, current_state, currentFuncName())

        # для раскрытия customfield_* запрашиваем карту имён (expand=names), кроме raw-режима.
        # В POST /search поле expand ДОЛЖНО быть списком (не строкой) — иначе Jira отвечает 400.
        expand_parts = [p.strip() for p in (expand or "").split(",") if p.strip()]
        if not raw_flag and "names" not in expand_parts:
            expand_parts.append("names")

        data = []
        start_at = 0
        page_size = min(limit, 100)
        while len(data) < limit:
            body = {"jql": jql, "startAt": start_at, "maxResults": page_size}
            if fields:
                body["fields"] = fields
            if expand_parts:
                body["expand"] = expand_parts
            response = request_with_retry(lambda: requests.post(f"{url}/rest/api/2/search", headers=headers, json=body, verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
            if response.status_code != 200:
                return False, f"jira search_issues http {response.status_code} ({response.text[:512]})", currentFuncName(), []
            payload = response.json()
            issues = payload.get("issues", [])
            if not issues:
                break
            names = payload.get("names") if not raw_flag else None
            for issue in issues:
                data.append(issue if raw_flag else _unfold_issue(issue, names))
                if len(data) >= limit:
                    break
            start_at += len(issues)
            if start_at >= payload.get("total", 0):
                break

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(data)} issues", currentFuncName(), current_state))
        return True, str(len(data)), currentFuncName(), data

    except Exception as e:
        error_message = f"jira search_issues fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_jira_get_issue(parameters, source_object, data_map, current_state):
    """Полная информация о заявке по её ID/ключу (GET /rest/api/2/issue/{id}).

    Параметры: issue_id -- id или ключ (напр. SD-123); expand -- (опц.) напр. 'changelog,renderedFields';
    flatten -- (опц.) уплощить. Возврат: list из одного dict."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        issue_id = query["issue_id"]
        expand = query.get("expand") or ""
        raw_flag = _as_bool(query.get("raw", False))

        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)
        # повторы транзиентных неудач (сеть/таймаут/429/5xx) — параметры из конфига источника
        retry_kwargs = retry_config(source, current_state, currentFuncName())

        # для раскрытия customfield_* запрашиваем карту имён (expand=names), кроме raw-режима
        request_expand = expand if raw_flag else _expand_with_names(expand)
        request_params = {}
        if request_expand:
            request_params["expand"] = request_expand

        response = request_with_retry(lambda: requests.get(f"{url}/rest/api/2/issue/{issue_id}", headers=headers, params=request_params, verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
        if response.status_code != 200:
            return False, f"jira get_issue http {response.status_code} ({response.text[:512]})", currentFuncName(), []

        issue = response.json()
        record = issue if raw_flag else _unfold_issue(issue, issue.get("names"))
        logger_log(syslog.LOG_DEBUG, get_log_message("done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), [record]

    except Exception as e:
        error_message = f"jira get_issue fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_jira_get_issue_changelog(parameters, source_object, data_map, current_state):
    """История изменений заявки (expand=changelog), развёрнутая в плоские строки.

    Параметры: issue_id -- id или ключ; raw -- (опц.) вернуть исходные histories без раскрытия.
    Возврат: list of dict — по строке на каждый item изменения (с метаданными history и заявки):
      issue_id, issue_key, id (history), author_*, created, field, fieldtype, from, fromString, to, toString."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        issue_id = query["issue_id"]
        raw_flag = _as_bool(query.get("raw", False))

        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)
        # повторы транзиентных неудач (сеть/таймаут/429/5xx) — параметры из конфига источника
        retry_kwargs = retry_config(source, current_state, currentFuncName())

        response = request_with_retry(lambda: requests.get(f"{url}/rest/api/2/issue/{issue_id}", headers=headers, params={"expand": "changelog"}, verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
        if response.status_code != 200:
            return False, f"jira get_issue_changelog http {response.status_code} ({response.text[:512]})", currentFuncName(), []

        issue = response.json()
        issue_key = issue.get("key")
        histories = (issue.get("changelog") or {}).get("histories", []) or []

        if raw_flag:
            return True, str(len(histories)), currentFuncName(), histories

        rows = []
        for history in histories:
            base = {k: v for k, v in history.items() if k != "items"}   # id, author, created
            items = history.get("items") or []
            if not items:
                row = flatten_data(base)
                row["issue_id"] = issue_id
                row["issue_key"] = issue_key
                rows.append(row)
                continue
            for item in items:
                row = flatten_data({**base, **(item if isinstance(item, dict) else {"item": item})})
                row["issue_id"] = issue_id
                row["issue_key"] = issue_key
                rows.append(row)

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(rows)} changelog rows", currentFuncName(), current_state))
        return True, str(len(rows)), currentFuncName(), rows

    except Exception as e:
        error_message = f"jira get_issue_changelog fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_jira_get_issue_comments(parameters, source_object, data_map, current_state):
    """Комментарии заявки в виде таблицы (GET /rest/api/2/issue/{id}/comment, с пагинацией).

    Параметры: issue_id -- id или ключ; limit -- максимум комментариев; raw -- (опц.) без раскрытия.
    Возврат: list of dict — по строке на комментарий (id, author_*, body, created, updated, ... + issue_id)."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        issue_id = query["issue_id"]
        try:
            limit = int(query["limit"]) if query.get("limit") else 100
        except (TypeError, ValueError):
            limit = 100
        raw_flag = _as_bool(query.get("raw", False))

        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)
        # повторы транзиентных неудач (сеть/таймаут/429/5xx) — параметры из конфига источника
        retry_kwargs = retry_config(source, current_state, currentFuncName())

        data = []
        start_at = 0
        page_size = min(limit, 100)
        while len(data) < limit:
            request_params = {"startAt": start_at, "maxResults": page_size}
            response = request_with_retry(lambda: requests.get(f"{url}/rest/api/2/issue/{issue_id}/comment", headers=headers, params=request_params, verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
            if response.status_code != 200:
                return False, f"jira get_issue_comments http {response.status_code} ({response.text[:512]})", currentFuncName(), []
            payload = response.json()
            comments = payload.get("comments", [])
            if not comments:
                break
            for comment in comments:
                if raw_flag:
                    data.append(comment)
                else:
                    row = flatten_data(comment)
                    row["issue_id"] = issue_id
                    data.append(row)
                if len(data) >= limit:
                    break
            start_at += len(comments)
            if start_at >= payload.get("total", 0):
                break

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(data)} comments", currentFuncName(), current_state))
        return True, str(len(data)), currentFuncName(), data

    except Exception as e:
        error_message = f"jira get_issue_comments fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_jira_get_issue_worklogs(parameters, source_object, data_map, current_state):
    """Журналы работ (worklog) заявки таблицей (GET /rest/api/2/issue/{id}/worklog, с пагинацией).

    Параметры: issue_id; limit -- максимум записей; raw -- (опц.) без раскрытия.
    Возврат: list of dict (id, author_*, comment, started, timeSpent, timeSpentSeconds, ... + issue_id)."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        issue_id = query["issue_id"]
        try:
            limit = int(query["limit"]) if query.get("limit") else 100
        except (TypeError, ValueError):
            limit = 100
        raw_flag = _as_bool(query.get("raw", False))

        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)
        # повторы транзиентных неудач (сеть/таймаут/429/5xx) — параметры из конфига источника
        retry_kwargs = retry_config(source, current_state, currentFuncName())

        data = []
        start_at = 0
        page_size = min(limit, 100)
        while len(data) < limit:
            request_params = {"startAt": start_at, "maxResults": page_size}
            response = request_with_retry(lambda: requests.get(f"{url}/rest/api/2/issue/{issue_id}/worklog", headers=headers, params=request_params, verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
            if response.status_code != 200:
                return False, f"jira get_issue_worklogs http {response.status_code} ({response.text[:512]})", currentFuncName(), []
            payload = response.json()
            worklogs = payload.get("worklogs", [])
            if not worklogs:
                break
            for worklog in worklogs:
                if raw_flag:
                    data.append(worklog)
                else:
                    row = flatten_data(worklog)
                    row["issue_id"] = issue_id
                    data.append(row)
                if len(data) >= limit:
                    break
            start_at += len(worklogs)
            if start_at >= payload.get("total", 0):
                break

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(data)} worklogs", currentFuncName(), current_state))
        return True, str(len(data)), currentFuncName(), data

    except Exception as e:
        error_message = f"jira get_issue_worklogs fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def _fetch_issue_field_list(parameters, source_object, current_state, field_name):
    """Получить заявку с одним полем-списком (attachment/issuelinks) и вернуть (ok, info, rows).
    Каждый элемент раскрывается в плоский dict + issue_id (raw=true -> исходные объекты)."""
    import requests
    query = parameters
    source = source_object

    issue_id = query["issue_id"]
    raw_flag = _as_bool(query.get("raw", False))
    verify = source["verify"] if "verify" in source else True
    timeout = source["timeout"] if "timeout" in source else 60
    url = source["url"].rstrip("/")
    headers = _jira_headers(source)
    retry_kwargs = retry_config(source, current_state, currentFuncName())

    response = request_with_retry(lambda: requests.get(f"{url}/rest/api/2/issue/{issue_id}", headers=headers, params={"fields": field_name}, verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
    if response.status_code != 200:
        return False, f"http {response.status_code} ({response.text[:512]})", []

    items = (response.json().get("fields") or {}).get(field_name)
    if not isinstance(items, list):
        items = []

    rows = []
    for item in items:
        if raw_flag:
            rows.append(item)
        else:
            row = flatten_data(item) if isinstance(item, dict) else {"value": item}
            if isinstance(row, dict):
                row["issue_id"] = issue_id
            rows.append(row)
    return True, str(len(rows)), rows


def execute_jira_get_issue_attachments(parameters, source_object, data_map, current_state):
    """Вложения заявки таблицей (поле fields.attachment). Тело файла не извлекается —
    возвращаются метаданные и ссылка на скачивание (поле content).

    Параметры: issue_id; raw -- (опц.). Возврат: list of dict (filename, size, mimeType, content (URL),
    author_*, created, ... + issue_id)."""
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        ok, info, rows = _fetch_issue_field_list(parameters, source_object, current_state, "attachment")
        if not ok:
            logger_log(syslog.LOG_ERR, get_log_message(f"jira get_issue_attachments {info}", currentFuncName(), current_state))
            return False, f"jira get_issue_attachments {info}", currentFuncName(), []
        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {info} attachments", currentFuncName(), current_state))
        return True, info, currentFuncName(), rows
    except Exception as e:
        error_message = f"jira get_issue_attachments fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_jira_get_issue_issuelinks(parameters, source_object, data_map, current_state):
    """Связи заявки таблицей (поле fields.issuelinks).

    Параметры: issue_id; raw -- (опц.). Возврат: list of dict (type_name, type_inward/outward,
    inwardIssue_key, outwardIssue_key, *_fields_summary, *_fields_status_name, ... + issue_id)."""
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        ok, info, rows = _fetch_issue_field_list(parameters, source_object, current_state, "issuelinks")
        if not ok:
            logger_log(syslog.LOG_ERR, get_log_message(f"jira get_issue_issuelinks {info}", currentFuncName(), current_state))
            return False, f"jira get_issue_issuelinks {info}", currentFuncName(), []
        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {info} issuelinks", currentFuncName(), current_state))
        return True, info, currentFuncName(), rows
    except Exception as e:
        error_message = f"jira get_issue_issuelinks fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def _cmdb_path_identifier(value):
    """Идентификатор объекта CMDB для подстановки в ПУТЬ URL -> (ok, safe_or_error).

    Пропускаем только id/ключи вида 5762496 / HAM-2727707: без слэшей, точек-переходов и пробелов,
    поэтому подстановка не может увести запрос на другой путь. Дополнительно квотируем (для набора
    выше это no-op, но страхует при расширении набора)."""
    from urllib.parse import quote
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", text):
        return False, f"некорректный идентификатор объекта CMDB: {text!r} (ожидается id или ключ вида HAM-123)"
    return True, quote(text, safe="")


def _cmdb_shape(query):
    """Форма вывода CMDB: table (по умолчанию) | long | flat | raw.

    Старые параметры поддерживаются как синонимы: flatten=true -> flat, raw=true -> raw."""
    shape = str(query.get("shape") or "").strip().lower()
    if shape in ("table", "wide", "long", "tidy", "flat", "flatten", "raw"):
        return {"wide": "table", "tidy": "long", "flatten": "flat"}.get(shape, shape)
    if _as_bool(query.get("raw", False)):
        return "raw"
    if _as_bool(query.get("flatten", False)):
        return "flat"
    return "table"


def _cmdb_api_base(cmdb_path):
    """Базовый путь Insight/Assets API из пути поиска: /rest/insight/1.0/iql/objects -> /rest/insight/1.0.

    Нужен для догрузки имён атрибутов (`/objecttype/{id}/attributes`)."""
    path = (cmdb_path or "").rstrip("/")
    for suffix in ("/iql/objects", "/aql/objects", "/objects"):
        if path.endswith(suffix):
            return path[: -len(suffix)]
    return "/rest/insight/1.0"


def _fetch_object_type_attribute_names(url, api_base, object_type_id, headers, verify, timeout,
                                       current_state, proxies=None, retry_kwargs=None):
    """Имена атрибутов типа объекта: GET {api_base}/objecttype/{id}/attributes -> {str(id): имя}.

    id типа проверяется как целое (подстановка в путь URL); ошибка/недоступность не роняют выборку —
    колонки останутся с именами `attr_<id>`."""
    import requests
    names = {}
    try:
        type_id = int(str(object_type_id).strip())
    except (TypeError, ValueError):
        logger_log(syslog.LOG_WARNING, get_log_message(
            f"cmdb attribute names: non-numeric objectType id {object_type_id!r} skipped",
            currentFuncName(), current_state))
        return names
    try:
        response = request_with_retry(lambda: requests.get(f"{url}{api_base}/objecttype/{type_id}/attributes",
                                headers=headers, verify=verify, timeout=timeout, proxies=proxies),
                                     **(retry_kwargs or {}))
        if response.status_code != 200:
            logger_log(syslog.LOG_WARNING, get_log_message(
                f"cmdb attribute names for objectType {type_id}: http {response.status_code}",
                currentFuncName(), current_state))
            return names
        for item in response.json() or []:
            if isinstance(item, dict) and item.get("id") is not None:
                label = item.get("name") or item.get("label")
                if label:
                    names[str(item["id"])] = str(label)
    except Exception as e:
        logger_log(syslog.LOG_WARNING, get_log_message(
            f"cmdb attribute names for objectType {type_id} fail: {str(e)}",
            currentFuncName(), current_state))
    return names


def _resolve_cmdb_attribute_names(objects, names, url, api_base, headers, verify, timeout,
                                  current_state, proxies=None, retry_kwargs=None):
    """Дополнить карту id->имя: имена из самих объектов (Assets) + догрузка по типам, где имён не хватает."""
    resolved = dict(names or {})
    for attr_id, label in attribute_names_from_objects(objects).items():
        resolved.setdefault(attr_id, label)
    for object_type_id in object_type_ids_with_unnamed_attributes(objects, resolved):
        fetched = _fetch_object_type_attribute_names(url, api_base, object_type_id, headers, verify,
                                                    timeout, current_state, proxies, retry_kwargs)
        for attr_id, label in fetched.items():
            resolved.setdefault(attr_id, label)
    return resolved


def _shape_cmdb_objects(objects, shape, names, query):
    """Применить выбранную форму вывода к сырым объектам CMDB."""
    if shape == "raw":
        return objects
    if shape == "flat":
        return [flatten_data(obj) if isinstance(obj, dict) else {"value": obj} for obj in objects]
    if shape == "long":
        return cmdb_objects_to_long(objects, names)
    sep = query.get("sep") if query.get("sep") not in (None, "") else "; "
    try:
        max_values = int(query["max_values"]) if query.get("max_values") else 0
    except (TypeError, ValueError):
        max_values = 0
    return cmdb_objects_to_table(objects, names, sep=str(sep), max_values=max_values)


def execute_jira_search_cmdb(parameters, source_object, data_map, current_state):
    """Поиск в CMDB JSM (Assets/Insight) по AQL.

    По умолчанию используется эндпоинт Insight Data Center: GET {cmdb_path}?iql=...&page=N&resultPerPage=...
    (cmdb_path = /rest/insight/1.0/iql/objects; для новых Assets -> /rest/assets/1.0/iql/objects).
    Параметры: aql -- запрос AQL/IQL; limit -- максимум объектов; cmdb_path -- (опц.) путь эндпоинта;
    shape -- (опц.) форма вывода: table (по умолчанию, колонка на атрибут по его имени) | long
    (строка на каждое значение) | flat (уплощение как раньше) | raw (исходный JSON);
    sep/max_values -- (опц.) склейка и ограничение мультизначных атрибутов в форме table;
    resolve_names -- (опц., по умолч. true) догружать имена атрибутов по типам объектов.
    Возврат: list of dict."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        aql = query["aql"]
        try:
            limit = int(query["limit"]) if query.get("limit") else 50
        except (TypeError, ValueError):
            limit = 50
        cmdb_path = query.get("cmdb_path") or source.get("cmdb_path") or "/rest/insight/1.0/iql/objects"
        shape = _cmdb_shape(query)
        named_shape = shape in ("table", "long")
        resolve_names = _as_bool(query.get("resolve_names", True), default=True)

        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)
        # повторы транзиентных неудач (сеть/таймаут/429/5xx) — параметры из конфига источника
        retry_kwargs = retry_config(source, current_state, currentFuncName())

        objects = []
        names = {}
        page = 1
        result_per_page = min(limit, 100)
        while len(objects) < limit:
            request_params = {"iql": aql, "page": page, "resultPerPage": result_per_page, "includeAttributes": "true"}
            if named_shape:
                # карта id -> имя атрибута приходит в том же ответе (иначе в атрибутах только objectTypeAttributeId)
                request_params["includeTypeAttributes"] = "true"
            response = request_with_retry(lambda: requests.get(f"{url}{cmdb_path}", headers=headers, params=request_params, verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
            if response.status_code != 200:
                return False, f"jira search_cmdb http {response.status_code} ({response.text[:512]})", currentFuncName(), []
            payload = response.json()
            entries = payload.get("objectEntries", [])
            if named_shape:
                for attr_id, label in attribute_name_map(payload).items():
                    names.setdefault(attr_id, label)
            if not entries:
                break
            for obj in entries:
                objects.append(obj)
                if len(objects) >= limit:
                    break
            if len(entries) < result_per_page:
                break
            page += 1

        if named_shape and resolve_names:
            # в выборке могут быть объекты разных типов (у каждого свои id атрибутов) -> добираем по типам
            names = _resolve_cmdb_attribute_names(objects, names, url, _cmdb_api_base(cmdb_path),
                                                  headers, verify, timeout, current_state,
                                                  proxies_from_source(source), retry_kwargs)
        data = _shape_cmdb_objects(objects, shape, names, query)

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(objects)} objects, {len(data)} rows ({shape})",
                                                     currentFuncName(), current_state))
        return True, str(len(data)), currentFuncName(), data

    except Exception as e:
        error_message = f"jira search_cmdb fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def _extract_cmdb_entries(payload):
    """Вытащить список объектов из ответа Insight-поиска (форма ответа зависит от версии):
    список -> как есть; dict -> первый непустой список под известными ключами."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("objectEntries", "results", "objects", "entries", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def execute_jira_search_cmdb_freetext(parameters, source_object, data_map, current_state):
    """Свободнотекстовый поиск в CMDB JSM (Insight) через механизм FREETEXT.

    Использует эндпоинт Insight: GET {search_path}?criteria=<freetext>&criteriaType=FREETEXT
    &schema=<id>&attributes=<list>&offset=N&limit=... (search_path по умолчанию /rest/insight-am/1/search).
    Параметры: freetext -- искомый текст (обяз.); schema -- (опц.) id схемы Insight; attributes -- (опц.)
    список/строка возвращаемых атрибутов; limit -- максимум объектов; search_path -- (опц.) путь эндпоинта;
    shape -- (опц.) форма вывода: table (по умолчанию) | long | flat | raw (как в search_cmdb).
    Возврат: list of dict."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        freetext = str(query.get("freetext") or "").strip()
        if not freetext:
            error_message = "jira search_cmdb_freetext: freetext is required"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []
        try:
            limit = int(query["limit"]) if query.get("limit") else 50
        except (TypeError, ValueError):
            limit = 50
        schema = query.get("schema") if query.get("schema") not in (None, "") else source.get("insight_schema")
        attributes = query.get("attributes") or source.get("insight_attributes") or "Key,Object Type,Label"
        if isinstance(attributes, list):
            attributes = ",".join(str(a) for a in attributes)
        search_path = query.get("search_path") or source.get("insight_search_path") or "/rest/insight-am/1/search"
        shape = _cmdb_shape(query)
        named_shape = shape in ("table", "long")
        resolve_names = _as_bool(query.get("resolve_names", True), default=True)
        api_base = _cmdb_api_base(query.get("cmdb_path") or source.get("cmdb_path"))

        verify = source["verify"] if "verify" in source else True
        # таймаут можно поднять на вызов (FREETEXT-поиск CMDB тяжелее обычного search)
        try:
            timeout = int(query["timeout"]) if query.get("timeout") else (source["timeout"] if "timeout" in source else 60)
        except (TypeError, ValueError):
            timeout = 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)
        # повторы транзиентных неудач (сеть/таймаут/429/5xx) — параметры из конфига источника
        retry_kwargs = retry_config(source, current_state, currentFuncName())

        # без schema FREETEXT сканирует все схемы -> частые таймауты; предупреждаем
        if schema in (None, ""):
            logger_log(syslog.LOG_WARNING, get_log_message(
                "search_cmdb_freetext without 'schema' may be slow/time out — pass schema=<id>",
                currentFuncName(), current_state))

        objects = []
        names = {}
        offset = 0
        page_size = min(limit, 50)   # FREETEXT тяжелее обычного search — меньшая страница безопаснее
        while len(objects) < limit:
            request_params = {"criteria": freetext, "criteriaType": "FREETEXT",
                              "attributes": attributes, "offset": offset, "limit": page_size}
            if schema not in (None, ""):
                request_params["schema"] = schema
            response = request_with_retry(lambda: requests.get(f"{url}{search_path}", headers=headers, params=request_params, verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
            if response.status_code != 200:
                return False, f"jira search_cmdb_freetext http {response.status_code} ({response.text[:512]})", currentFuncName(), []
            payload = response.json()
            entries = _extract_cmdb_entries(payload)
            if named_shape:
                for attr_id, label in attribute_name_map(payload).items():
                    names.setdefault(attr_id, label)
            if not entries:
                break
            for obj in entries:
                objects.append(obj)
                if len(objects) >= limit:
                    break
            if len(entries) < page_size:
                break
            offset += len(entries)

        if named_shape and resolve_names:
            names = _resolve_cmdb_attribute_names(objects, names, url, api_base,
                                                  headers, verify, timeout, current_state,
                                                  proxies_from_source(source), retry_kwargs)
        data = _shape_cmdb_objects(objects, shape, names, query)

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(objects)} objects, {len(data)} rows ({shape})",
                                                     currentFuncName(), current_state))
        return True, str(len(data)), currentFuncName(), data

    except Exception as e:
        error_message = f"jira search_cmdb_freetext fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def _unfold_cmdb_audit(entry, object_key):
    """Одна запись истории CMDB -> плоская строка.

    Автор разворачивается в author_* (avatarUrl отбрасываем — как аватары в таблице объектов),
    objectKey добавляется в строку, чтобы историю нескольких объектов (напр. через APPLY) можно было
    склеивать и группировать одной таблицей."""
    if not isinstance(entry, dict):
        return {"objectKey": object_key, "value": entry}
    row = {"objectKey": object_key}
    for field in ("occurredAt", "type", "action", "title", "message", "id"):
        if field in entry:
            row[field] = entry.get(field)
    author = entry.get("author")
    if isinstance(author, dict):
        for source_field, target_field in (("key", "author_key"), ("name", "author_name"),
                                           ("displayName", "author_displayName"), ("active", "author_active")):
            if source_field in author:
                row[target_field] = author.get(source_field)
    elif author is not None:
        row["author"] = author
    # прочие поля записи (кроме уже разобранных и шумных) — чтобы не потерять данные при смене API
    for field, value in entry.items():
        if field in ("author", "occurredAt", "type", "action", "title", "message", "id"):
            continue
        row[field] = flatten_data(value) if isinstance(value, dict) else value
    return row


def _audit_in_period(entry, since, until):
    """Попадает ли запись в период по occurredAt (ISO-строки сравниваются лексикографически)?"""
    occurred = str((entry or {}).get("occurredAt") or "")
    if since and occurred and occurred < str(since):
        return False
    if until and occurred and occurred > str(until):
        return False
    return True


def execute_jira_get_cmdb_history(parameters, source_object, data_map, current_state):
    """История (audit log) объекта CMDB JSM: кто и когда менял поля.

    Эндпоинт Insight AM: GET {audits_path}/{object_key}/audits?limit=&offset=&order=&type=&criteria=
    (audits_path по умолчанию /rest/insight-am/1/assets).
    Параметры: object_key -- ключ объекта, напр. HAM-2727707 (обяз.; ключ, а не числовой id);
    limit -- максимум записей (пагинация по offset); criteria -- (опц.) текстовый фильтр на стороне
    Jira, напр. 'Изменение поля «Description»'; order -- (опц.) MOST_RECENT (по умолч.) | LEAST_RECENT;
    type -- (опц.) тип записей, по умолчанию AUDIT (пустое значение -> параметр не отправляется);
    since/until -- (опц.) отсечь по occurredAt на нашей
    стороне (ISO, напр. 2026-01-01); audits_path -- (опц.) путь эндпоинта; raw -- (опц.) исходный JSON.
    Возврат: list of dict (плоские строки: objectKey, occurredAt, type, action, title, message, author_*)."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        key_ok, object_key = _cmdb_path_identifier(query.get("object_key"))
        if not key_ok:
            error_message = ("jira get_cmdb_history: object_key is required (e.g. HAM-2727707)"
                             if not str(query.get("object_key") or "").strip()
                             else f"jira get_cmdb_history: {object_key}")
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []
        try:
            limit = int(query["limit"]) if query.get("limit") else 50
        except (TypeError, ValueError):
            limit = 50
        criteria = query.get("criteria") if query.get("criteria") not in (None, "") else None
        order = query.get("order") or "MOST_RECENT"
        # type не задан -> AUDIT (как в типовом запросе); задан пустым -> параметр не отправляем вовсе
        # (запасной ход, если у API другой набор/значение по умолчанию)
        audit_type = "AUDIT" if "type" not in query else (query.get("type") or None)
        since = query.get("since") or None
        until = query.get("until") or None
        audits_path = query.get("audits_path") or source.get("insight_audits_path") or "/rest/insight-am/1/assets"
        raw_flag = _as_bool(query.get("raw", False))

        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)
        # повторы транзиентных неудач (сеть/таймаут/429/5xx) — параметры из конфига источника
        retry_kwargs = retry_config(source, current_state, currentFuncName())

        data = []
        offset = 0
        page_size = min(limit, 100)
        total = None
        while len(data) < limit:
            request_params = {"limit": page_size, "offset": offset, "order": order}
            if audit_type:
                request_params["type"] = audit_type
            if criteria:
                request_params["criteria"] = criteria       # requests сам кодирует кириллицу в URL
            response = request_with_retry(lambda: requests.get(f"{url}{audits_path}/{object_key}/audits", headers=headers,
                                    params=request_params, verify=verify, timeout=timeout,
                                    **proxy_kwargs(source)), **retry_kwargs)
            if response.status_code != 200:
                return (False, f"jira get_cmdb_history http {response.status_code} ({response.text[:512]})",
                        currentFuncName(), [])
            payload = response.json()
            entries = payload.get("results") if isinstance(payload, dict) else payload
            entries = entries if isinstance(entries, list) else []
            metadata = payload.get("metadata") if isinstance(payload, dict) else None
            if isinstance(metadata, dict) and isinstance(metadata.get("total"), int):
                total = metadata["total"]
            if not entries:
                break
            for entry in entries:
                if not _audit_in_period(entry, since, until):
                    continue
                data.append(entry if raw_flag else _unfold_cmdb_audit(entry, object_key))
                if len(data) >= limit:
                    break
            offset += len(entries)
            if len(entries) < page_size or (total is not None and offset >= total):
                break

        info = f"{len(data)}" if total is None else f"{len(data)} of {total}"
        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {info} audit records", currentFuncName(), current_state))
        return True, info, currentFuncName(), data

    except Exception as e:
        error_message = f"jira get_cmdb_history fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_jira_get_cmdb_object(parameters, source_object, data_map, current_state):
    """Один объект CMDB JSM (Assets/Insight) по id или ключу.

    Эндпоинт Insight: GET {cmdb_object_path}/{object_id} (cmdb_object_path по умолчанию
    /rest/insight/1.0/object). Часть версий Insight отдаёт объект БЕЗ блока attributes — тогда атрибуты
    догружаются вторым запросом GET {cmdb_object_path}/{object_id}/attributes. Если по ключу
    (HAM-123) эндпоинт объект не отдал, делается фолбэк на поиск по AQL `objectKey = "<ключ>"`.

    Параметры: object_id -- id (5762496) ИЛИ ключ (HAM-2727707) — текст (обяз.);
    shape -- (опц.) форма вывода: table (по умолчанию, колонка на атрибут по его имени) | long | flat |
    raw; sep/max_values -- (опц.) склейка и ограничение мультизначных атрибутов; resolve_names --
    (опц., по умолч. true) догружать имена атрибутов; cmdb_object_path/cmdb_path -- (опц.) пути
    эндпоинтов. Возврат: list из одного dict (пустой список, если объекта нет)."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        id_ok, object_id = _cmdb_path_identifier(query.get("object_id"))
        if not id_ok:
            error_message = f"jira get_cmdb_object: {object_id}"
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []

        object_path = (query.get("cmdb_object_path") or source.get("cmdb_object_path")
                       or "/rest/insight/1.0/object")
        cmdb_path = query.get("cmdb_path") or source.get("cmdb_path") or "/rest/insight/1.0/iql/objects"
        shape = _cmdb_shape(query)
        named_shape = shape in ("table", "long")
        resolve_names = _as_bool(query.get("resolve_names", True), default=True)

        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)
        retry_kwargs = retry_config(source, current_state, currentFuncName())

        response = request_with_retry(lambda: requests.get(f"{url}{object_path}/{object_id}", headers=headers,
                                                           verify=verify, timeout=timeout,
                                                           **proxy_kwargs(source)), **retry_kwargs)
        cmdb_object = None
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict) and (payload.get("id") is not None or payload.get("objectKey")):
                cmdb_object = payload
        elif response.status_code not in (400, 404):
            return (False, f"jira get_cmdb_object http {response.status_code} ({response.text[:512]})",
                    currentFuncName(), [])

        # ключ вместо числового id: не все версии принимают его в пути -> ищем объект по AQL
        if cmdb_object is None and not object_id.isdigit():
            iql_response = request_with_retry(
                lambda: requests.get(f"{url}{cmdb_path}", headers=headers,
                                     params={"iql": f'objectKey = "{object_id}"', "page": 1,
                                             "resultPerPage": 1, "includeAttributes": "true",
                                             "includeTypeAttributes": "true"},
                                     verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
            if iql_response.status_code != 200:
                return (False, f"jira get_cmdb_object http {iql_response.status_code} "
                               f"({iql_response.text[:512]})", currentFuncName(), [])
            iql_payload = iql_response.json()
            entries = iql_payload.get("objectEntries") or []
            if entries:
                cmdb_object = entries[0]

        if cmdb_object is None:
            logger_log(syslog.LOG_WARNING, get_log_message(
                f"cmdb object '{object_id}' not found", currentFuncName(), current_state))
            return True, "0", currentFuncName(), []

        # атрибуты приходят не во всех версиях Insight -> добираем отдельным запросом
        if not cmdb_object.get("attributes"):
            attributes_response = request_with_retry(
                lambda: requests.get(f"{url}{object_path}/{object_id}/attributes", headers=headers,
                                     verify=verify, timeout=timeout, **proxy_kwargs(source)), **retry_kwargs)
            if attributes_response.status_code == 200:
                attributes = attributes_response.json()
                if isinstance(attributes, list):
                    cmdb_object["attributes"] = attributes
            else:
                logger_log(syslog.LOG_WARNING, get_log_message(
                    f"cmdb object '{object_id}' attributes http {attributes_response.status_code}",
                    currentFuncName(), current_state))

        names = {}
        if named_shape:
            if resolve_names:
                names = _resolve_cmdb_attribute_names([cmdb_object], names, url, _cmdb_api_base(cmdb_path),
                                                      headers, verify, timeout, current_state,
                                                      proxies_from_source(source), retry_kwargs)
            else:
                names = attribute_names_from_objects([cmdb_object])
        data = _shape_cmdb_objects([cmdb_object], shape, names, query)

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, object '{object_id}' ({shape})",
                                                     currentFuncName(), current_state))
        return True, str(len(data)), currentFuncName(), data

    except Exception as e:
        error_message = f"jira get_cmdb_object fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
