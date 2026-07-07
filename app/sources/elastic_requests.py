import syslog
from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
import app.sources.additional.elastic2python as elastic2python


def _make_retry_logger(current_state, func_name):
    """Callback для retry_call: пишет факт каждого повтора запроса в лог (WARNING)."""
    def on_retry(attempt, err, delay):
        status = getattr(err, "status", None)
        detail = f"status {status}" if status else f"{type(err).__name__}: {err}"
        logger_log(syslog.LOG_WARNING, get_log_message(
            f"elastic request retry attempt {attempt} after {delay:.2f}s ({detail})", func_name, current_state))
    return on_retry


def _make_debug_logger(current_state, func_name):
    """Callback для пустого результата: логирует matched (hits.total), подсказку и фрагмент ответа elastic,
    чтобы понять «почему 0» — фильтр ничего не нашёл или потеряны fields при _source:false."""
    def debug_log(meta):
        logger_log(syslog.LOG_WARNING, get_log_message(
            f"elastic returned 0 rows: matched={meta.get('matched')} ({meta.get('hint')}); "
            f"response sample: {meta.get('response_sample')}", func_name, current_state))
    return debug_log

# execution_function
def _auth_kwargs(source):
    """Параметры аутентификации для data_taxi_*_requests из конфигурации источника.
    По умолчанию api_key (обратная совместимость); basic_auth берёт логин из source['key']['account']."""
    return {"auth_type": source.get("auth_type", "api_key"),
            "auth_user": (source.get("key") or {}).get("account")}


def execute_elastic_query(parameters, source_object, data_map, current_state):
    try:
        query = parameters
        source = source_object
        auth_kwargs = _auth_kwargs(source)

        # опциональные параметры
        if "search_after_shift" not in query:
            query["search_after_shift"] = -10
        if "size" not in query:
            query["size"] = 1000
        if "limit" not in query:
            query["limit"] = -1

        logger_log(syslog.LOG_DEBUG, get_log_message(f"start", currentFuncName(), current_state))
        data_taxi_requests_result = elastic2python.data_taxi_requests(
            query["url"],
            f'{current_state["app_name"]}/{current_state["app_version"]}',
            source["key"]["value"],
            source.get("verify_certs", False),
            source.get("request_timeout", 300),
            query["query"],
            query["sort"],
            query["fields"],
            query["size"],
            query["search_after_shift"],
            query["limit"],
            debug = False,
            max_retries=source.get("max_retries", 2),
            retry_backoff=source.get("retry_backoff_seconds", 0.5),
            retry_statuses=tuple(source.get("retry_on_status", [429, 502, 503, 504])),
            on_retry=_make_retry_logger(current_state, currentFuncName()),
            debug_log=_make_debug_logger(current_state, currentFuncName()),
            **auth_kwargs)
        if data_taxi_requests_result[0] == False:
            error_message = f"data_taxi_requests_result is false: {data_taxi_requests_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []
        
        logger_log(syslog.LOG_DEBUG, get_log_message(f"done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), data_taxi_requests_result[3]

    except BaseException as e:
        error_message = f"first query fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


def execute_elastic_aggs(parameters, source_object, data_map, current_state):
    source = source_object
    query = parameters
    auth_kwargs = _auth_kwargs(source)
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message(f"start", currentFuncName(), current_state))
        data_taxi_aggs_requests_result = elastic2python.data_taxi_aggs_requests(
            query["url"],
            f'{current_state["app_name"]}/{current_state["app_version"]}',
            source["key"]["value"],
            source.get("verify_certs", False),
            source.get("request_timeout", 300),
            query["query"],
            query["aggs"],
            debug = False,
            size = 0,
            max_retries=source.get("max_retries", 2),
            retry_backoff=source.get("retry_backoff_seconds", 0.5),
            retry_statuses=tuple(source.get("retry_on_status", [429, 502, 503, 504])),
            on_retry=_make_retry_logger(current_state, currentFuncName()),
            **auth_kwargs)

        if data_taxi_aggs_requests_result[0] == False:
            error_message = f"data_taxi_aggs_requests_result is false: {data_taxi_aggs_requests_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []
        
        logger_log(syslog.LOG_DEBUG, get_log_message(f"done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), data_taxi_aggs_requests_result[3]

    except BaseException as e:
        error_message = f"query fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
    
# функция получения списка индексов/паттернов (что вообще можно смотреть существующими функциями)
def execute_elastic_list_indices(parameters, source_object, data_map, current_state):
    """Список индексов/алиасов через console-proxy. url задаёт цель, напр.
    .../api/console/proxy?path=/_cat/indices?format=json&method=GET (или _cat/aliases, _resolve/index/*).
    Возврат — list-of-dict (для _cat/*?format=json это готовые строки index/health/docs.count/... )."""
    source = source_object
    query = parameters
    auth_kwargs = _auth_kwargs(source)
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message(f"start", currentFuncName(), current_state))
        list_result = elastic2python.data_taxi_list_requests(
            query["url"],
            f'{current_state["app_name"]}/{current_state["app_version"]}',
            source["key"]["value"],
            source.get("verify_certs", False),
            source.get("request_timeout", 300),
            debug = False,
            max_retries=source.get("max_retries", 2),
            retry_backoff=source.get("retry_backoff_seconds", 0.5),
            retry_statuses=tuple(source.get("retry_on_status", [429, 502, 503, 504])),
            on_retry=_make_retry_logger(current_state, currentFuncName()),
            **auth_kwargs)
        if list_result[0] == False:
            error_message = f"list_result is false: {list_result[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), list_result[3]

    except BaseException as e:
        error_message = f"list indices fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []


# функция построения цепочки иерархии для выбранного процесса pid
def execute_function_linux_pid_hierarchy_elastic_requests(parameters, source_object, data_map, current_state):
    source = source_object
    query = parameters
    auth_kwargs = _auth_kwargs(source)
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message(f"start", currentFuncName(), current_state))
        current_data = []

        # получаем данные по целевому pid
        try:
            data_taxi_requests_result = elastic2python.data_taxi_requests(
                query["url"], 
                f'{current_state["app_name"]}/{current_state["app_version"]}',
                source["key"]["value"], 
                source.get("verify_certs", False), 
                source.get("request_timeout", 300),
                query["query"], 
                query["sort"], 
                query["fields"], 
                query["size"], 
                query["search_after_shift"], 
                debug = False,
                **auth_kwargs)
            if data_taxi_requests_result[0] == False:
                error_message = f"data_taxi_requests_result is false: {data_taxi_requests_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), []
            
            response_data = data_taxi_requests_result[3]
        
        except BaseException as e:
            error_message = f"target pid query fail: {str(e)}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []   

        if len(response_data) == 0:
            error_message = f"target pid no data (response_data len is 0)"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []   

        for data in response_data: #бывает так, что в логах в один момент времени есть 2 процесса с одинаковым pid (возможно нужны наносекунды)
            data["hierarchy_position"] = 0
        current_data = current_data + response_data

        # получаем данные по иерархии
        # ищем родителей
        parent_deep = parameters["parent_deep"]
        position = 0
        while parent_deep > 0:
            parent_deep = parent_deep - 1
            if "process.parent.pid" in current_data[-1]:
                position = position - 1
                #parent_deep = parent_deep - 1
                # подготовка фильтров. в пид подставляем родительский пид
                #print(current_data[-1])
                current_elastic_query = query["query"]
                target_parent_pid = current_data[-1]["process.parent.pid"]
                current_elastic_query["bool"]["filter"][2]["match_phrase"] = {}
                current_elastic_query["bool"]["filter"][2]["match_phrase"]["process.pid"] = target_parent_pid
                current_elastic_query["bool"]["filter"][0]["range"][parameters["timestamp_field"]]["gte"] = parameters["gte"] #от левого края предела поиска
                current_elastic_query["bool"]["filter"][0]["range"][parameters["timestamp_field"]]["lte"] = current_data[-1][parameters["timestamp_field"]] # до времени целевого процесса, родитель появился раньше согласно принципцу причинности
                
                try:
                    data_taxi_requests_result = elastic2python.data_taxi_requests(
                        query["url"], 
                        f'{current_state["app_name"]}/{current_state["app_version"]}',
                        source["key"]["value"], 
                        source.get("verify_certs", False), 
                        source.get("request_timeout", 300),
                        current_elastic_query, 
                        query["sort"], 
                        query["fields"], 
                        query["size"], 
                        query["search_after_shift"], 
                        debug = False,
                **auth_kwargs)
                    if data_taxi_requests_result[0] == False:
                        error_message = f"data_taxi_requests_result is false: {data_taxi_requests_result[1]}"
                        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                        return False, error_message, currentFuncName(), []
                    
                    response_data = data_taxi_requests_result[3]
                except BaseException as e:
                    error_message = f"parent pid query fail: {str(e)}"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), []
                
                # если мы прошли успешно запрос, то обрабатываем ответ        
                if len(response_data) > 0:
                    # если вдруг найдено много,берём с самым ближайшим временем к целевому процессу
                    # мы можем это сделать, так как у нас есть сортировка
                    
                    response_data[0]["hierarchy_position"] = position
                    current_data.append(response_data[0])

        # ищем детей
        child_deep = parameters["child_deep"]
        position = 0
        # поскольку детей может быть много, запоминать, кого смотреть, будем через списки
        target_child_pid_list = [] # для текущей итерации
        target_child_pid_list_buf = [] # подготовительный для следующей итерации
        target_child_pid_list_done = [] # запоминалка, кого уже посмотрели, на случай дублей
        target_child_pid_list_buf.append({"pid":current_data[0]["process.pid"],"timestamp":current_data[0][parameters["timestamp_field"]]})
        while child_deep > 0:
            position = position + 1 # счётчик позиции для иерархии
            child_deep = child_deep - 1 # счётчик глубины
            target_child_pid_list = target_child_pid_list_buf # принимаем подготовленный список
            target_child_pid_list_buf = [] # очищаем подготавливаемый
            for pid in target_child_pid_list: # обходим все пиды, что нужно проверить, и ищем их детей
                if pid not in target_child_pid_list_done:
                    target_child_pid_list_done.append(pid) # запоминаем, чтобы его больше не смотреть
                    # подготавливаем фильтр
                    current_elastic_query = query["query"]
                    
                    current_elastic_query["bool"]["filter"][2]["match_phrase"] = {}
                    current_elastic_query["bool"]["filter"][2]["match_phrase"]["process.parent.pid"] = pid["pid"]
                    current_elastic_query["bool"]["filter"][0]["range"][parameters["timestamp_field"]]["gte"] = pid["timestamp"] # время от текущего пида
                    current_elastic_query["bool"]["filter"][0]["range"][parameters["timestamp_field"]]["lte"] = parameters["lte"] # до указанного предела в будущее
                    
                    try:
                        data_taxi_requests_result = elastic2python.data_taxi_requests(
                            query["url"], 
                            f'{current_state["app_name"]}/{current_state["app_version"]}',
                            source["key"]["value"], 
                            source.get("verify_certs", False), 
                            source.get("request_timeout", 300),
                            current_elastic_query, 
                            query["sort"], 
                            query["fields"], 
                            query["size"], 
                            query["search_after_shift"], 
                            debug = False,
                **auth_kwargs)
                        if data_taxi_requests_result[0] == False:
                            error_message = f"data_taxi_requests_result is false: {data_taxi_requests_result[1]}"
                            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                            return False, error_message, currentFuncName(), []
                        
                        response_data = data_taxi_requests_result[3]
                    except BaseException as e:
                        error_message = f"child pid query fail: {str(e)}"
                        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                        return False, error_message, currentFuncName(), []
            
                    if len(response_data) > 0:
                            # пробегаемся по ответу от эластика
                        for data in response_data:
                            data["hierarchy_position"] = position # проставляем иерархию
                            current_data.append(data) # добавляем данные
                            target_child_pid_list_buf.append({"pid":data["process.pid"],"timestamp":data[parameters["timestamp_field"]]}) # пишем в список на следующую проверку
        
        logger_log(syslog.LOG_DEBUG, get_log_message(f"done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), current_data
    except BaseException as e:
        error_message = f"generic fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
    
# функция получения сиблингов для выбранного процесса pid
def execute_function_linux_pid_siblings_elastic_requests(parameters, source_object, data_map, current_state):
    source = source_object
    query = parameters
    auth_kwargs = _auth_kwargs(source)
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message(f"start", currentFuncName(), current_state))
        current_data = []
        # получаем данные по целевому pid
        try:
            data_taxi_requests_result = elastic2python.data_taxi_requests(
                query["url"], 
                f'{current_state["app_name"]}/{current_state["app_version"]}',
                source["key"]["value"], 
                source.get("verify_certs", False), 
                source.get("request_timeout", 300),
                query["query"], 
                query["sort"], 
                query["fields"], 
                query["size"], 
                query["search_after_shift"], 
                debug = False,
                **auth_kwargs)
            if data_taxi_requests_result[0] == False:
                error_message = f"data_taxi_requests_result is false: {data_taxi_requests_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), []
                        
            response_data = data_taxi_requests_result[3]
        except BaseException as e:
            error_message = f"target pid query fail: {str(e)}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []

        if len(response_data) == 0:
            error_message = f"target pid no data (response_data len is 0)"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []

        current_data = current_data + response_data
        
        # получаем данные по сиблингам
        current_elastic_query = query["query"]
        target_parent_pid = current_data[-1]["process.parent.pid"]
        current_elastic_query["bool"]["filter"][2]["match_phrase"] = {}
        current_elastic_query["bool"]["filter"][2]["match_phrase"]["process.parent.pid"] = target_parent_pid
        current_elastic_query["bool"]["filter"][0]["range"][parameters["timestamp_field"]]["gte"] = parameters["gte"] #от левого края
        current_elastic_query["bool"]["filter"][0]["range"][parameters["timestamp_field"]]["lte"] = parameters["lte"] # до правого края
            
        # делаем запрос
        try:
            data_taxi_requests_result = elastic2python.data_taxi_requests(
                query["url"], 
                f'{current_state["app_name"]}/{current_state["app_version"]}',
                source["key"]["value"], 
                source.get("verify_certs", False), 
                source.get("request_timeout", 300),
                current_elastic_query, 
                query["sort"], 
                query["fields"], 
                query["size"], 
                query["search_after_shift"], 
                debug = False,
                **auth_kwargs)
            if data_taxi_requests_result[0] == False:
                error_message = f"data_taxi_requests_result is false: {data_taxi_requests_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), []
                        
            response_data = data_taxi_requests_result[3]
        except BaseException as e:
            error_message = f"siblings pid query fail: {str(e)}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []
        
        for data in response_data:
            current_data.append(data)

        logger_log(syslog.LOG_DEBUG, get_log_message(f"done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), current_data
    except BaseException as e:
        error_message = f"generic fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []