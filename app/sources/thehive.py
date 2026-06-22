import syslog
from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
from app.sources.additional.flatten import flatten_data


def execute_thehive_get_alerts(parameters, source_object, data_map, current_state):
    """Получение списка алертов из IRP TheHive 5.x по фильтру.

    Использует query-API TheHive (POST /api/v1/query): операции listAlert + опциональные
    filter/sort + page. На выходе — list of dict с полными объектами алертов (все поля).

    Параметры вызова (parameters):
      filter   -- операция фильтра TheHive (dict). Пустой {} означает «без фильтра».
                  Можно передать как полную операцию {"_name":"filter", "_field":"status", "_value":"New"},
                  так и тело фильтра без "_name" — оно будет обёрнуто в {"_name":"filter", ...}.
                  Сложные фильтры: {"_name":"filter","_and":[{"_field":"severity","_value":3}, ...]}.
      limit    -- максимальное число алертов (страница from=0..to=limit).
      sort     -- (опц.) операция сортировки TheHive, напр. {"_fields":[{"_createdAt":"desc"}]}.
      extra_data -- (опц.) список доп. вычисляемых полей TheHive (extraData), напр. ["case"].
      flatten  -- (опц., bool) уплощить вложенные поля каждого алерта (по умолчанию False —
                  объекты возвращаются как есть, со всеми полями).
    """
    source = source_object
    query = parameters
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))

        # --- конфигурация источника ---
        token = source["key"]["value"]
        url = source["url"].rstrip("/")
        timeout = source["timeout"] if "timeout" in source else 60
        verify = source["verify"] if "verify" in source else False

        # --- параметры вызова ---
        alert_filter = query["filter"]
        limit = query["limit"]
        flatten_flag = query["flatten"] if "flatten" in query else False
        extra_data = query["extra_data"] if "extra_data" in query else []

        # --- сборка цепочки query-операций TheHive ---
        operations = [{"_name": "listAlert"}]

        if isinstance(alert_filter, dict) and len(alert_filter) > 0:
            if "_name" in alert_filter:
                operations.append(alert_filter)
            else:
                operations.append({"_name": "filter", **alert_filter})

        if "sort" in query and isinstance(query["sort"], dict) and len(query["sort"]) > 0:
            sort_op = query["sort"]
            if "_name" not in sort_op:
                sort_op = {"_name": "sort", **sort_op}
            operations.append(sort_op)

        operations.append({"_name": "page", "from": 0, "to": limit, "extraData": extra_data})

        body = {"query": operations}

        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}'
        }

        response = requests.post(
            f"{url}/api/v1/query?name=alerts",
            headers=headers,
            json=body,
            timeout=timeout,
            verify=verify
        )

        if response.status_code != 200:
            error_message = f"fail: TheHive response code is {response.status_code} ({response.text[:512]})"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []

        if 'application/json' not in response.headers.get('Content-Type', ''):
            error_message = f"fail: TheHive response is not application/json"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []

        alerts = response.json()

        # listAlert+page возвращает JSON-массив; на случай обёртки достаём список из dict
        if isinstance(alerts, dict):
            alerts = alerts.get("data", alerts.get("alerts", []))

        if isinstance(alerts, list) == False:
            error_message = f"fail: TheHive alerts payload is not a list"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []

        if flatten_flag:
            output = [flatten_data(alert) for alert in alerts]
        else:
            output = alerts

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(output)} alerts", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), output

    except Exception as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
