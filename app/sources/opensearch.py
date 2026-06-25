import syslog
from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
import app.sources.additional.elastic2python as elastic2python


# execution_function
def execute_opensearch_query(parameters, source_object, data_map, current_state):
    source = source_object
    query = parameters
    from opensearchpy import OpenSearch
    # создаём объект подключения к эластику
    try:
        if source["auth_type"] == "http_auth":
            client = OpenSearch(
                hosts = [{'host': source["host"], 'port': source["port"]}],
                http_compress = source.get("http_compress", True), # enables gzip compression for request bodies
                http_auth = (source["key"]["account"], source["key"]["value"]),
                use_ssl = source.get("use_ssl", True),
                verify_certs = source.get("verify_certs", False),
                ssl_assert_hostname = source.get("ssl_assert_hostname", False),
                ssl_show_warn = source.get("ssl_show_warn", False),
                timeout=source.get("timeout", 300),
                max_retries=source.get("max_retries", 2),
                retry_on_timeout=source.get("retry_on_timeout", True)
            )
        else:
            error_message = f"unknown source auth_type {source["auth_type"]}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []        
    except BaseException as e:
        error_message = f"create client fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
    
    # проверяем создание объекта подключения
    if client is None:
        error_message = f"create client is None"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []   
    
    # выполняем запрос    
    try: 
        data_taxi_status = elastic2python.data_taxi(
                elastic_client = client, 
                index = query["index"], 
                query = query["query"], 
                sort = query["sort"], 
                fields = query["fields"], 
                size = query["size"], 
                search_after_shift = query["search_after_shift"], 
                limit = query["limit"],
                debug = False
        )
        if data_taxi_status[0] == False:
            error_message = f"data_taxi_status is false: {data_taxi_status[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []
        
        return True, "OK", currentFuncName(), data_taxi_status[3]
    
    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
    
def execute_opensearch_aggs(parameters, source_object, data_map, current_state):
    source = source_object
    query = parameters
    from opensearchpy import OpenSearch
    # создаём объект подключения к эластику
    try:
        if source["auth_type"] == "http_auth":
            client = OpenSearch(
                hosts = [{'host': source["host"], 'port': source["port"]}],
                http_compress = source.get("http_compress", True), # enables gzip compression for request bodies
                http_auth = (source["key"]["account"], source["key"]["value"]),
                use_ssl = source.get("use_ssl", True),
                verify_certs = source.get("verify_certs", False),
                ssl_assert_hostname = source.get("ssl_assert_hostname", False),
                ssl_show_warn = source.get("ssl_show_warn", False),
                timeout=source.get("timeout", 300),
                max_retries=source.get("max_retries", 2),
                retry_on_timeout=source.get("retry_on_timeout", True)
            )
        else:
            error_message = f"unknown source auth_type {source["auth_type"]}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []        
    except BaseException as e:
        error_message = f"create client fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
    
    # проверяем создание объекта подключения
    if client is None:
        error_message = f"create client is None"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []   
    
    # выполняем запрос
    try:   
        data_taxi_aggs_status = elastic2python.data_taxi_aggs(
            elastic_client = client,
            index = query["index"], 
            query = query["query"],
            size = 0,
            aggs = query["aggs"],
            debug = False
        )
        if data_taxi_aggs_status[0] == False:
            error_message = f"data_taxi_aggs_status is false: {data_taxi_aggs_status[1]}"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), []
        return True, "OK", currentFuncName(), data_taxi_aggs_status[3]
    except BaseException as e:
        error_message = f"query fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []