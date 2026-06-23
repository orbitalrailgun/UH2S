import ipaddress
import syslog
from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
from app.sources.additional.flatten import flatten_data

# Типы объектов для общего поиска (как в строке поиска NetBox). Перекрывается параметром object_types.
NETBOX_DEFAULT_OBJECT_TYPES = [
    "ipam/ip-addresses",
    "ipam/prefixes",
    "ipam/ip-ranges",
    "ipam/aggregates",
    "ipam/vlans",
    "ipam/vrfs",
    "dcim/devices",
    "dcim/interfaces",
    "dcim/sites",
    "dcim/racks",
    "virtualization/virtual-machines",
    "virtualization/interfaces",
    "tenancy/tenants",
    "tenancy/contacts",
    "circuits/circuits",
]


def _netbox_headers(source):
    return {
        'Authorization': f'Token {source["key"]["value"]}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }


def execute_netbox_search(parameters, source_object, data_map, current_state):
    """Общий поиск по NetBox (REST API 4.x), аналог строки поиска.

    Параметры:
      target        -- строка поиска (передаётся как ?q= в каждый тип объекта);
      object_types  -- (опц.) список путей API для поиска (по умолчанию NETBOX_DEFAULT_OBJECT_TYPES);
      limit         -- (опц.) максимум записей на тип объекта (по умолчанию 50);
      flatten       -- (опц., bool) уплощить вложенные поля (по умолчанию False — полные объекты).

    Возврат: list of dict — найденные объекты со всеми полями, помеченные полем object_type."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        target = query["target"]
        object_types = query["object_types"] if query.get("object_types") else NETBOX_DEFAULT_OBJECT_TYPES
        limit = int(query["limit"]) if query.get("limit") else 50
        flatten_flag = bool(query.get("flatten", False))
        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60

        headers = _netbox_headers(source)
        base_url = source["url"].rstrip("/")

        data = []
        for object_type in object_types:
            collected = 0
            page_size = min(limit, 1000)
            next_url = f"{base_url}/api/{object_type}/?q={target}&limit={page_size}"
            while next_url and collected < limit:
                response = requests.get(next_url, headers=headers, verify=verify, timeout=timeout)
                if response.status_code != 200:
                    # не валим весь поиск из-за одного типа (может быть 400/403) — логируем и идём дальше
                    logger_log(syslog.LOG_WARNING, get_log_message(
                        f"netbox search {object_type} http {response.status_code}", currentFuncName(), current_state))
                    break
                payload = response.json()
                for result in payload.get("results", []):
                    record = flatten_data(result) if flatten_flag else result
                    if isinstance(record, dict):
                        record["object_type"] = object_type
                    data.append(record)
                    collected += 1
                    if collected >= limit:
                        break
                next_url = payload.get("next")

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done, {len(data)} objects", currentFuncName(), current_state))
        return True, str(len(data)), currentFuncName(), data

    except Exception as e:
        error_message = f"netbox search fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_netbox_search_cidr_by_ipaddress(parameters, source_object, data_map, current_state):
    """Поиск ближайшего наименьшего (наиболее специфичного) префикса, содержащего IP.

    Если отдельной записи об IP нет, возвращает самую узкую сеть из NetBox, в которую входит адрес.
    Параметры: target -- IP-адрес; flatten -- (опц.) уплощить результат.
    Возврат: list of dict (0 или 1 элемент)."""
    import requests
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        query = parameters
        source = source_object

        target = query["target"]
        flatten_flag = bool(query.get("flatten", False))
        verify = source["verify"] if "verify" in source else True
        timeout = source["timeout"] if "timeout" in source else 60

        headers = _netbox_headers(source)
        base_url = source["url"].rstrip("/")

        response = requests.get(f"{base_url}/api/ipam/prefixes/?contains={target}", headers=headers, verify=verify, timeout=timeout)
        if response.status_code != 200:
            return False, f"response.status_code is not 200: {response.status_code}", currentFuncName(), []

        results = response.json().get("results", [])
        if not results:
            return True, "not found", currentFuncName(), []

        # среди содержащих адрес префиксов выбираем самый специфичный (наибольшая длина маски)
        best = None
        best_prefixlen = -1
        for result in results:
            prefix = result.get("prefix")
            if not prefix:
                continue
            try:
                network = ipaddress.ip_network(prefix, strict=False)
            except ValueError:
                continue
            if network.prefixlen > best_prefixlen:
                best_prefixlen = network.prefixlen
                best = result

        if best is None:
            return True, "not found", currentFuncName(), []

        record = flatten_data(best) if flatten_flag else best
        logger_log(syslog.LOG_DEBUG, get_log_message("done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), [record]

    except Exception as e:
        error_message = f"netbox search_cidr_by_ip fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
