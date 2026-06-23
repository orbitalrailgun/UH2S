import syslog
from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
from app.sources.additional.flatten import flatten_data


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


def _unfold_issue(issue):
    """Развернуть заявку Jira в плоский dict: поднять вложенный 'fields' на верхний уровень
    и уплощить вложенные объекты/списки (status -> status_name, assignee -> assignee_displayName и т.п.).

    Поле 'comment' (коллекция комментариев) не разворачивается в столбцы, а сводится к
    comment_count (сами комментарии доступны через функцию get_issue_comments)."""
    if not isinstance(issue, dict):
        return issue
    merged = {k: v for k, v in issue.items() if k != "fields"}
    fields = issue.get("fields")
    if isinstance(fields, dict):
        fields = dict(fields)
        comment = fields.get("comment")
        if isinstance(comment, dict) and "comments" in comment:
            fields["comment_count"] = comment.get("total", len(comment.get("comments") or []))
            del fields["comment"]
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

        data = []
        start_at = 0
        page_size = min(limit, 100)
        while len(data) < limit:
            body = {"jql": jql, "startAt": start_at, "maxResults": page_size}
            if fields:
                body["fields"] = fields
            if expand:
                body["expand"] = expand
            response = requests.post(f"{url}/rest/api/2/search", headers=headers, json=body, verify=verify, timeout=timeout)
            if response.status_code != 200:
                return False, f"jira search_issues http {response.status_code} ({response.text[:512]})", currentFuncName(), []
            payload = response.json()
            issues = payload.get("issues", [])
            if not issues:
                break
            for issue in issues:
                data.append(issue if raw_flag else _unfold_issue(issue))
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

        request_params = {}
        if expand:
            request_params["expand"] = expand

        response = requests.get(f"{url}/rest/api/2/issue/{issue_id}", headers=headers, params=request_params, verify=verify, timeout=timeout)
        if response.status_code != 200:
            return False, f"jira get_issue http {response.status_code} ({response.text[:512]})", currentFuncName(), []

        issue = response.json()
        record = issue if raw_flag else _unfold_issue(issue)
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

        response = requests.get(f"{url}/rest/api/2/issue/{issue_id}", headers=headers, params={"expand": "changelog"}, verify=verify, timeout=timeout)
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

        data = []
        start_at = 0
        page_size = min(limit, 100)
        while len(data) < limit:
            request_params = {"startAt": start_at, "maxResults": page_size}
            response = requests.get(f"{url}/rest/api/2/issue/{issue_id}/comment", headers=headers, params=request_params, verify=verify, timeout=timeout)
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


def execute_jira_search_cmdb(parameters, source_object, data_map, current_state):
    """Поиск в CMDB JSM (Assets/Insight) по AQL.

    По умолчанию используется эндпоинт Insight Data Center: GET {cmdb_path}?iql=...&page=N&resultPerPage=...
    (cmdb_path = /rest/insight/1.0/iql/objects; для новых Assets -> /rest/assets/1.0/iql/objects).
    Параметры: aql -- запрос AQL/IQL; limit -- максимум объектов; cmdb_path -- (опц.) путь эндпоинта;
    flatten -- (опц.) уплощить. Возврат: list of dict (objectEntries)."""
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
        flatten_flag = _as_bool(query.get("flatten", False))

        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60
        url = source["url"].rstrip("/")
        headers = _jira_headers(source)

        data = []
        page = 1
        result_per_page = min(limit, 100)
        while len(data) < limit:
            request_params = {"iql": aql, "page": page, "resultPerPage": result_per_page, "includeAttributes": "true"}
            response = requests.get(f"{url}{cmdb_path}", headers=headers, params=request_params, verify=verify, timeout=timeout)
            if response.status_code != 200:
                return False, f"jira search_cmdb http {response.status_code} ({response.text[:512]})", currentFuncName(), []
            payload = response.json()
            entries = payload.get("objectEntries", [])
            if not entries:
                break
            for obj in entries:
                data.append(flatten_data(obj) if flatten_flag else obj)
                if len(data) >= limit:
                    break
            if len(entries) < result_per_page:
                break
            page += 1

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(data)} objects", currentFuncName(), current_state))
        return True, str(len(data)), currentFuncName(), data

    except Exception as e:
        error_message = f"jira search_cmdb fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
