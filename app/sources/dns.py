import json
import syslog
from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
# механизм разыменования dns-запросов

def execute_dns_resolve(parameters, source_object, data_map, current_state):
    source = source_object
    query = parameters
    import dns.resolver
    try:
        logger_log(syslog.LOG_DEBUG, get_log_message("start", currentFuncName(), current_state))
        dns_domain_name = parameters["domain_name"]
        result = []

        try:
            for answer in dns.resolver.resolve(dns_domain_name):
                node = {}
                node["domain"] = dns_domain_name
                node["address"] = answer.to_text()
                result.append(node)
        except dns.resolver.NoAnswer:
            # такое может быть вполне https://dnspython.readthedocs.io/en/latest/exceptions.html
            pass
        except dns.resolver.NotAbsolute:
            # такое может быть вполне
            pass
        except dns.resolver.NXDOMAIN:
            # такое имя не существует, норма
            pass


        logger_log(syslog.LOG_DEBUG, get_log_message("done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), result
    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), []
    

