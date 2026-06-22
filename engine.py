import re
import json
import multiprocessing
import syslog
from app.logging import get_log_message, logger_log, currentFuncName
#from app.validation import json_validate
from app.engine import command_parser, process_injections, get_source_function, get_command_dependency, run_command, run_apply_command, get_variable_type, get_notifier_function
from app.db import get_actual_object_by_name, get_secret, get_source_threads_pool, get_user_by_username


def commands_executor(commands:list,current_state:dict):
    # сначала последовательно считаем все def и calc
    variables = {}
    for command in commands:
        if command["command"] == "DEF":
            variables[command['variable_name']] = command['variable_value']
        if command["command"] == "CALC":
            # реализовать базовые функции integer, float, datetime, string
            pass
        # после присваивания идёт инъектирование. Инъектирование можно перенести и вне (после всех def и calc)
        if "parameters" in command:
            variables2command_injection_result = process_injections(command['parameters'], variables, current_state)
            if variables2command_injection_result[0] == False:
                error_message = f"var injection error: {variables2command_injection_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), {}
            command['parameters'] = variables2command_injection_result[3]
    
    # получаем данные по источнику данных и функции
    for command in commands:
        if command["command"] == "GET":
            #получаем исполняемый объект по имени, тут исполняемым обектом может быть source или script
            get_actual_object_by_name_result = get_actual_object_by_name(command["source"], "('source', 'script')", current_state)
            if not get_actual_object_by_name_result[0]:
                error_message = f"get object {command["source"]} error: {get_actual_object_by_name_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands
            
            source_object = get_actual_object_by_name_result[3]

            #check user roles
            allow = False
            for role in current_state["roles"]:
                if role == "fullmaster" or role in source_object["roles"]:
                    allow = True
                    break
            
            if not allow:
                error_message = f"source object {source_object["name"]} is not allow for user {current_state["username"]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands
            
            # получаем тип источника, если тип исполнения source
            if source_object["type"] == "source":
                if "type" not in source_object["json"]:
                    error_message = f"there is not type in json source object {source_object["name"]}"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), commands
                command["source_type"] = source_object["json"]["type"]

            #check source function
            if source_object["type"] == "source":
                get_source_function_result = get_source_function(command["source_type"],command["function"], current_state)
                if not get_source_function_result[0]:
                    error_message = f"get_source_function error: {get_source_function_result[1]}"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), commands
                command["function_parameters"] = get_source_function_result[3][0]
                command["function_object"] = get_source_function_result[3][1]

            #check parameters
            for parameter in command["function_parameters"]:
                if parameter not in command["parameters"]:
                    error_message = f"there is not parameter {parameter} for function {command["source_type"]}:{command["function"]}"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), commands
                if type(command["function_parameters"][parameter]) != type(command["parameters"][parameter]):
                    # дополнительная попытка переконвертации
                    get_variable_type_result = get_variable_type(command["parameters"][parameter], current_state)
                    if not get_variable_type_result:
                        error_message = f"recheck var type error for {parameter}"
                        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                        return False, error_message, currentFuncName(), commands
                    if type(command["function_parameters"][parameter]) != type(get_variable_type_result[3][1]):
                        error_message = f"wrong parameter type for {parameter} (put {type(parameter)} need {type(command["parameters"][parameter])})"
                        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                        return False, error_message, currentFuncName(), commands
                    command["parameters"][parameter] = get_variable_type_result[3][1]
            
            # get secrets
            if "key" in source_object["json"]:
                if isinstance(source_object["json"]["key"], dict):
                    if "system" in source_object["json"]["key"] and "account" in source_object["json"]["key"]:
                        get_secret_result = get_secret(source_object["json"]["key"]["system"], source_object["json"]["key"]["account"], current_state)
                        if not get_secret_result:
                            error_message = f"get secret error: {get_secret_result[1]}"
                            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                            return False, error_message, currentFuncName(), {}
                        source_object["json"]["key"]["value"] = get_secret_result[3]
            
            command["source_object"] = source_object

    # считаем зависимости по apply/ожидаем выполнение
    for command in commands:
        if command["command"] == "GET":
            get_command_dependency_result = get_command_dependency(command, current_state)
            if not get_command_dependency_result[0]:
                error_message = f"{get_command_dependency_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), {}
            command["dependency"] = get_command_dependency_result[3]

    # запуск и поэтапное выполнение согласно зависимостям

    result_map = {}
    stage = 0
    while True:
        # выделяем такие задачи, для которых зависимостей нет или они все доступны, и при этом они ещё не были выполнены
        stage_execute_commands = []
        for command in commands:
            if command["command"] == "GET" and command["data_name"] not in result_map:
                dependency_available = True
                for depend in command["dependency"]:
                    if depend not in result_map:
                        dependency_available = False
                if not dependency_available:
                    continue
                stage_execute_commands.append(command)
        # на данном этапе у нас есть список выполняемых команд
        if len(stage_execute_commands) == 0:
            # выходим из цикла выполнять больше нечего
            break
        
        # with multiprocessing.Pool(current_state["processes"]) as pool:
        #     for executed_command in stage_execute_commands:
        #         current_data_map = {key: result_map[key][3] for key in executed_command["dependency"]}
        #         results = {}
        #         print(executed_command)
        #         if "apply" in executed_command:
        #             results[executed_command["data_name"]] = pool.apply_async(run_apply_command, (executed_command, current_data_map, current_state))
        #         else:
        #             results[executed_command["data_name"]] = pool.apply_async(run_command, (executed_command, current_data_map, current_state))
                
        #         for r in results.keys():
        #             result_map[r] = results[r].get()

        for executed_command in stage_execute_commands:
            current_data_map = {key: result_map[key][3] for key in executed_command["dependency"]}
            results = {}
            print(executed_command)
            if "apply" in executed_command:
                results[executed_command["data_name"]] = run_apply_command(executed_command, current_data_map, current_state)
            else:
                results[executed_command["data_name"]] = run_command(executed_command, current_data_map, current_state)
                
            for r in results.keys():
                result_map[r] = results[r]


    # тут есть данные
    # обработка SAVE
    for command in commands:
        if command["command"] == "SAVE":
            # сохранение данных в storage
            pass

    # надо сделать системную переменную с результатом работы _execution_result_
    # Уведомления делаются в последнюю очередь
    for command in commands:
        if command["command"] == "NOTIFY":
            # {
            #     "command": "NOTIFY",
            #     "line": "mattermost(\"helloworld\")",
            #     "parsed": true,
            #     "parsed_comment": "Ok",
            #     "notifier": "mattermost",
            #     "message": "helloworld",
            #     "user": "harvester"
            # }
            #получаем исполняемый объект по имени, тут исполняемым обектом может быть source или script
            get_actual_object_by_name_result = get_actual_object_by_name(command["notifier"], "('notifier')", current_state)
            if not get_actual_object_by_name_result[0]:
                error_message = f"get object {command["notifier"]} error: {get_actual_object_by_name_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands
            
            notifier_object = get_actual_object_by_name_result[3]
            command["notifier_object"] = notifier_object
            # получаем данные пользователя
            get_user_by_username_result = get_user_by_username(current_state["username"], current_state)
            if not get_user_by_username_result[0]:
                error_message = f"get user {current_state["username"]} error: {get_user_by_username_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands
            username_json = get_user_by_username_result[3]["json"]
            
            if "notify" not in username_json:
                error_message = f"there is not notify node in user {current_state["username"]} json"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands

            if command["notifier"] not in username_json["notify"]:
                error_message = f"there is not notifier {command["notifier"]} in notify node user {current_state["username"]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands
            
            command["notifier_user_conf"] = username_json["notify"][command["notifier"]]

            # проверяем права пользователя, можно ли его нотифицировать
            #check user roles
            allow = False
            for role in current_state["roles"]:
                if role == "fullmaster" or role in notifier_object["roles"]:
                    allow = True
                    break

            if not allow:
                error_message = f"notifier object {source_object["name"]} is not allow for user {current_state["username"]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands
            
            # получаем функцию-исполнитель
            if "type" not in command["notifier_object"]["json"]:
                error_message = f"there is not type in notifier object {command["notifier"]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands
            
            get_notifier_function_result = get_notifier_function(command["notifier_object"]["json"]["type"], current_state)
            if not get_notifier_function_result[0]:
                error_message = f"get notifier function error: {get_notifier_function_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands
            
            command["notifier_function"] = get_notifier_function_result[3]

            # исполняем нотификацию
            notify_result = command["notifier_function"](command["notifier_object"]["json"], command["notifier_user_conf"], command["message"], current_state)
            
            



            


    # SHOW/PRINT не трогаем, это делает интерфейс
    return True, "Done", currentFuncName(), (variables, result_map)