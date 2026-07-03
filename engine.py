import re
import json
import multiprocessing
import syslog
import threading
from concurrent.futures import ThreadPoolExecutor
from app.logging import get_log_message, logger_log, currentFuncName
#from app.validation import json_validate
from app.engine import command_parser, process_injections, get_source_function, get_command_dependency, run_command, run_apply_command, run_load_command, run_save_storage_command, get_variable_type, get_notifier_function, execute_calc
from app.db import get_actual_object_by_name, get_secret, get_source_threads_pool, get_user_by_username


def _function_hint(source_type, function, current_state):
    """Подсказка по параметрам функции источника (типы + примеры) — для понятных ошибок пользователю."""
    try:
        from app.engine import describe_source_functions_struct
        spec = describe_source_functions_struct(source_type)
        if not isinstance(spec, dict):
            return ""
        for f in spec.get("functions", []):
            if f.get("function") == function:
                req = "; ".join(f"{name}:{meta.get('type')}={json.dumps(meta.get('example'), ensure_ascii=False)}"
                                for name, meta in (f.get("required") or {}).items()) or "—"
                opt = ", ".join((f.get("optional") or {}).keys()) or "—"
                return f"ожидаются параметры {source_type}:{function} — обязательные: {req}; опциональные: {opt}"
    except BaseException:
        pass
    return ""


def commands_executor(commands:list,current_state:dict,injected_variables:dict=None):
    # сначала последовательно считаем все def и calc
    # injected_variables — параметры, переданные при вызове скрипта; перекрывают его DEF
    injected_variables = injected_variables or {}
    variables = dict(injected_variables)
    for command in commands:
        if command["command"] == "DEF":
            if command['variable_name'] not in injected_variables:
                def_value = command['variable_value']
                # инъекция %(name)X в значение DEF из уже определённых переменных (порядок важен:
                # ссылаться можно на переменные, объявленные выше по скрипту)
                if "%(" in json.dumps(def_value, ensure_ascii=False):
                    def_injection = process_injections({"__def__": def_value}, variables, current_state)
                    if def_injection[0] == False:
                        error_message = f"DEF var injection error: {def_injection[1]}"
                        if "_status" in command:
                            command["_status"] = "error"
                            command["_info"] = error_message
                        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                        return False, error_message, currentFuncName(), {}
                    def_value = def_injection[3]["__def__"]
                variables[command['variable_name']] = def_value
        if command["command"] == "CALC":
            calc_result = execute_calc(command, variables, current_state)
            if not calc_result[0]:
                if "_status" in command:
                    command["_status"] = "error"
                    command["_info"] = calc_result[1]
                logger_log(syslog.LOG_ERR, get_log_message(f"{calc_result[1]}", currentFuncName(), current_state))
                return False, calc_result[1], currentFuncName(), {}
            variables[command["result_name"]] = calc_result[3]
        # после присваивания идёт инъектирование. Инъектирование можно перенести и вне (после всех def и calc)
        if "parameters" in command:
            variables2command_injection_result = process_injections(command['parameters'], variables, current_state)
            if variables2command_injection_result[0] == False:
                error_message = f"var injection error: {variables2command_injection_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), {}
            command['parameters'] = variables2command_injection_result[3]
        # прогресс (UI): DEF/CALC выполняются здесь же
        if command.get("_status") == "pending" and command["command"] in ("DEF", "CALC"):
            command["_status"] = "done"

    # получаем данные по источнику данных и функции
    for command in commands:
        if command["command"] == "GET":
            # вызов сохранённого скрипта: GET script:<script_name>(params) AS result
            # 'script' — зарезервированное ключевое слово в позиции source; имя объекта в позиции function
            if command["source"] == "script":
                get_script_object_result = get_actual_object_by_name(command["function"], "('script')", current_state)
                if not get_script_object_result[0]:
                    error_message = f"get script object {command["function"]} error: {get_script_object_result[1]}"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), commands

                script_object = get_script_object_result[3]

                # проверка ролей на объекте-скрипте
                allow = False
                for role in current_state["roles"]:
                    if role == "fullmaster" or role in script_object["roles"]:
                        allow = True
                        break
                if not allow:
                    error_message = f"script object {script_object["name"]} is not allow for user {current_state["username"]}"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), commands

                # сверка DEF скрипта и переданных параметров
                script_json = script_object["json"]
                if "script" not in script_json:
                    command["_status"] = "error"
                    command["_info"] = "у объекта скрипта нет тела 'script'"
                    error_message = f"script object {script_object["name"]} has no 'script' body"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), commands

                sub_commands = command_parser(script_json["script"], current_state)
                bad_sub = [c for c in sub_commands if not c.get("parsed", True)]
                if bad_sub:
                    command["_status"] = "error"
                    command["_info"] = f"ошибка парсинга тела скрипта: {bad_sub[0].get("parsed_comment", "?")}"
                    error_message = f"script {script_object["name"]} body parse error: {bad_sub[0].get("parsed_comment", "?")}"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), commands

                def_names = set(c["variable_name"] for c in sub_commands if c["command"] == "DEF" and "variable_name" in c)
                param_names = set(command["parameters"].keys())

                # параметр без соответствующего DEF -> error (с подсказкой допустимых параметров)
                extra_params = param_names - def_names
                if extra_params:
                    available = ", ".join(sorted(def_names)) if def_names else "(в скрипте нет DEF)"
                    command["_status"] = "error"
                    command["_info"] = f"параметры без DEF: {", ".join(sorted(extra_params))}. Доступные параметры (DEF): {available}"
                    error_message = f"script {script_object["name"]}: unknown parameters {", ".join(sorted(extra_params))}; available params (DEF): {available}"
                    logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                    return False, error_message, currentFuncName(), commands

                # DEF без переданного параметра (захардкожено) -> warning
                uncovered_defs = def_names - param_names
                if uncovered_defs:
                    command["_warning"] = f"DEF без входного параметра (захардкожено): {", ".join(sorted(uncovered_defs))}"

                command["is_script"] = True
                command["source_type"] = "script"     # чтобы get_command_dependency не падал
                command["function_parameters"] = {}    # у скрипта нет схемы required-параметров
                command["script_object"] = script_object
                command["source_object"] = script_object
                command["sub_commands"] = sub_commands  # переиспользуем при исполнении (без повторного парсинга)
                continue

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

            #check parameters — все ли обязательные параметры переданы
            missing = [p for p in command["function_parameters"] if p not in command["parameters"]]
            if missing:
                hint = _function_hint(command["source_type"], command["function"], current_state)
                provided = ", ".join(command["parameters"].keys()) or "—"
                error_message = (f"не хватает обязательных параметров {missing} для "
                                 f"{command['source_type']}:{command['function']}. Передано: [{provided}]. {hint}")
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), commands
            for parameter in command["function_parameters"]:
                if type(command["function_parameters"][parameter]) != type(command["parameters"][parameter]):
                    # только имя и типы, без значений (значение параметра может быть секретом); DEBUG, не INFO
                    logger_log(syslog.LOG_DEBUG, get_log_message(
                        f"param '{parameter}' type coerce: {type(command['parameters'][parameter]).__name__} "
                        f"-> {type(command['function_parameters'][parameter]).__name__}", currentFuncName(), current_state))
                    # дополнительная попытка переконвертации
                    get_variable_type_result = get_variable_type(command["parameters"][parameter], current_state)
                    if not get_variable_type_result:
                        error_message = f"recheck var type error for {parameter}"
                        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                        return False, error_message, currentFuncName(), commands
                    if type(command["function_parameters"][parameter]) != type(get_variable_type_result[3][1]):
                        expected = command["function_parameters"][parameter]
                        hint = _function_hint(command["source_type"], command["function"], current_state)
                        error_message = (f"неверный тип параметра '{parameter}' для {command['source_type']}:{command['function']}: "
                                         f"передано {type(command['parameters'][parameter]).__name__} "
                                         f"({command['parameters'][parameter]}); ожидается {type(expected).__name__} "
                                         f"(пример: {json.dumps(expected, ensure_ascii=False)}). {hint}")
                        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                        return False, error_message, currentFuncName(), commands
                    command["parameters"][parameter] = get_variable_type_result[3][1]

            # best-effort типизация остальных (необязательных) параметров: движок проверяет
            # типы только required, поэтому опц. параметры приходят сырыми строками из парсера.
            # Приводим к типу там, где get_variable_type уверенно распознаёт (list/dict/int/float/bool/quoted-string).
            for optional_parameter in command["parameters"]:
                value = command["parameters"][optional_parameter]
                if isinstance(value, str):
                    optional_type_result = get_variable_type(value, current_state)
                    if optional_type_result[0]:
                        command["parameters"][optional_parameter] = optional_type_result[3][1]

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

    # считаем зависимости по apply/ожидаем выполнение.
    # storage-команды (SAVE→storage / LOAD / GET LOAD) линеаризуются по ключу в порядке скрипта,
    # чтобы работало чтение-после-записи в одном запуске: SAVE(k) выше -> виден LOAD(k)/GET LOAD(k) ниже.
    last_token = {}   # storage key -> data_name последнего писателя (SAVE-storage или GET LOAD refresh)
    save_storage_index = 0
    # имена, которые реально появятся в result_map (результаты GET/LOAD) — чтобы SAVE зависел от них;
    # если таблица SAVE — это DEF-переменная, зависимость не добавляем (она доступна сразу)
    produced_names = {c.get("data_name") for c in commands if c["command"] in ("GET", "LOAD") and c.get("data_name")}
    for command in commands:
        if command["command"] == "GET":
            get_command_dependency_result = get_command_dependency(command, current_state)
            if not get_command_dependency_result[0]:
                error_message = f"{get_command_dependency_result[1]}"
                logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
                return False, error_message, currentFuncName(), {}
            # оставляем только зависимости, которые РЕАЛЬНО производит какой-то шаг (GET/LOAD).
            # Имена из FROM/JOIN, которых никто не создаёт (базовые таблицы/опечатки), не должны
            # блокировать шаг навсегда — иначе он «зависает» в pending (см. duckdb_im/sqlite3_im).
            command["dependency"] = [d for d in get_command_dependency_result[3] if d in produced_names]
            cache = command.get("load_cache")
            if cache:
                key = cache["id"]
                if key in last_token:
                    command["dependency"] = command["dependency"] + [last_token[key]]
                if cache.get("refresh"):
                    last_token[key] = command["data_name"]   # GET LOAD refresh пишет ключ
        elif command["command"] == "LOAD":
            key = command["load_id"]
            command["dependency"] = [last_token[key]] if key in last_token else []
        elif command["command"] == "SAVE" and command.get("save_is_storage"):
            key = command["storage_key"]
            command["data_name"] = f"__save__{key}__{save_storage_index}"   # синтетический токен
            save_storage_index += 1
            deps = []
            if command["storage_dataname"] in produced_names:   # таблица-результат GET/LOAD
                deps.append(command["storage_dataname"])
            if key in last_token:                               # порядок относительно прежней записи
                deps.append(last_token[key])
            command["dependency"] = deps
            last_token[key] = command["data_name"]

    # по-источниковые семафоры: ограничивают число одновременных обращений к одному источнику.
    # Лимит — max_threads из source-объекта (если задан), иначе глобальный processes.
    default_workers = max(1, int(current_state.get("processes", 4) or 4))
    source_semaphores = {}
    for command in commands:
        if command["command"] == "GET" and "source_object" in command and command.get("source"):
            name = command["source"]
            if name not in source_semaphores:
                try:
                    n = int((command["source_object"].get("json") or {}).get("max_threads") or default_workers)
                except (TypeError, ValueError):
                    n = default_workers
                source_semaphores[name] = threading.Semaphore(max(1, n))
    current_state["_source_semaphores"] = source_semaphores

    # диспетчеризация одного шага (для стадийного пула); исключение нормализуем в кортеж-ошибку,
    # чтобы падение шага не роняло весь пул
    def _dispatch_step(executed_command, current_data_map):
        try:
            kind = executed_command["command"]
            if kind == "LOAD":
                return run_load_command(executed_command, current_state)
            if kind == "SAVE":
                return run_save_storage_command(executed_command, current_data_map, variables, current_state)
            if executed_command.get("is_script"):
                if "apply" in executed_command:
                    return run_apply_script_command(executed_command, current_data_map, current_state)
                return run_script_command(executed_command, current_data_map, current_state)
            if "apply" in executed_command:
                return run_apply_command(executed_command, current_data_map, current_state)
            return run_command(executed_command, current_data_map, current_state)
        except BaseException as step_exception:
            return False, f"crashed: {step_exception}", currentFuncName(), {}

    # запуск и поэтапное выполнение согласно зависимостям

    result_map = {}
    stage = 0
    while True:
        # выделяем такие задачи, для которых зависимостей нет или они все доступны, и при этом они ещё не были выполнены
        stage_execute_commands = []
        for command in commands:
            schedulable = command["command"] in ("GET", "LOAD") or \
                (command["command"] == "SAVE" and command.get("save_is_storage"))
            if schedulable and command["data_name"] not in result_map:
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
        
        # параллельное исполнение независимых команд стадии (потоки — исполнитель уже в run.io_bound;
        # общий result_map пишется только здесь, после сбора futures). data_map каждой команды строится
        # до сабмита (зависимости уже в result_map с прошлых стадий).
        pool_size = max(1, int(current_state.get("processes", 4) or 4))
        for executed_command in stage_execute_commands:
            logger_log(syslog.LOG_DEBUG, get_log_message(
                f"exec {executed_command.get('command', '?')} "
                f"{executed_command.get('source', '')}:{executed_command.get('function', '')} "
                f"-> {executed_command.get('data_name', '?')}", currentFuncName(), current_state))
            if executed_command.get("_status") == "pending":
                executed_command["_status"] = "running"

        stage_data_maps = {
            c["data_name"]: {key: result_map[key][3] for key in c["dependency"]}
            for c in stage_execute_commands
        }
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            futures = {
                c["data_name"]: pool.submit(_dispatch_step, c, stage_data_maps[c["data_name"]])
                for c in stage_execute_commands
            }

        # сбор строго в порядке стадии; статусы шагов; прогон останавливается на первой ошибке
        # (стадия при этом уже доработала — детерминированные статусы)
        first_error = None
        for executed_command in stage_execute_commands:
            step_result = futures[executed_command["data_name"]].result()
            result_map[executed_command["data_name"]] = step_result
            if "_status" in executed_command:
                if step_result[0]:
                    executed_command["_status"] = "warning" if executed_command.get("_warning") else "done"
                    executed_command["_info"] = executed_command.get("_warning") or step_result[1]
                else:
                    executed_command["_status"] = "error"
                    executed_command["_info"] = step_result[1]
            if not step_result[0] and first_error is None:
                first_error = f"step '{executed_command.get('data_name', '?')}' failed: {step_result[1]}"
        if first_error is not None:
            logger_log(syslog.LOG_ERR, get_log_message(first_error, currentFuncName(), current_state))
            return False, first_error, currentFuncName(), commands

    # страховка: если после цикла остались незапущенные планируемые шаги (недостижимая/циклическая
    # зависимость) — это ошибка, а не молчаливый успех (иначе PRINT/SHOW отработают по пустым данным).
    for command in commands:
        schedulable = command["command"] in ("GET", "LOAD") or \
            (command["command"] == "SAVE" and command.get("save_is_storage"))
        if schedulable and command.get("data_name") not in result_map:
            missing = [d for d in command.get("dependency", []) if d not in result_map]
            error_message = (f"step '{command.get('data_name', '?')}' not executed: "
                             f"unresolved dependency {missing}")
            if "_status" in command:
                command["_status"] = "error"
                command["_info"] = error_message
            logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
            return False, error_message, currentFuncName(), commands

    # SAVE→storage теперь исполняется в основном цикле (как планируемый шаг, с учётом порядка
    # относительно LOAD по тому же ключу). Файловый SAVE рендерится в UI/API.

    # надо сделать системную переменную с результатом работы _execution_result_
    # Уведомления делаются в последнюю очередь
    for command in commands:
        if command["command"] == "NOTIFY":
            if command.get("_status") == "pending":
                command["_status"] = "running"
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
            if "_status" in command:
                command["_status"] = "done"
            
            



            


    # SHOW/PRINT не трогаем, это делает интерфейс
    return True, "Done", currentFuncName(), (variables, result_map)


MAX_SCRIPT_DEPTH = 10

def _type_script_params(parameters, current_state):
    """Типизация параметров вызова -> значения-переменные для под-скрипта."""
    injected = {}
    for key, value in parameters.items():
        if isinstance(value, str):
            get_variable_type_result = get_variable_type(value, current_state)
            injected[key] = get_variable_type_result[3][1] if get_variable_type_result[0] else value
        else:
            injected[key] = value
    return injected


def _execute_script(command, injected_variables, current_state):
    """Исполнить под-скрипт command['script_object'] с заданными injected_variables.
    Возвращает (ok, info, func, data) — data по json['return'] (таблица или переменная).
    Защита от рекурсии/циклов через current_state['_script_stack']."""
    script_object = command["script_object"]
    script_json = script_object["json"]
    script_name = script_object["name"]

    if "script" not in script_json:
        return False, f"script object '{script_name}' has no 'script' body", currentFuncName(), {}
    return_name = script_json.get("return")
    if not return_name:
        return False, f"script object '{script_name}' has no 'return' name", currentFuncName(), {}

    script_stack = current_state.get("_script_stack", [])
    if script_name in script_stack:
        return False, f"script recursion cycle: {' -> '.join(script_stack + [script_name])}", currentFuncName(), {}
    if len(script_stack) >= MAX_SCRIPT_DEPTH:
        return False, f"script recursion too deep (>{MAX_SCRIPT_DEPTH})", currentFuncName(), {}
    sub_state = dict(current_state)
    sub_state["_script_stack"] = script_stack + [script_name]

    # под-скрипт уже распарсен и провалидирован на этапе резолва
    sub_commands = command.get("sub_commands") or command_parser(script_json["script"], sub_state)
    sub_result = commands_executor(sub_commands, sub_state, injected_variables)
    if not sub_result[0]:
        return False, f"script '{script_name}' error: {sub_result[1]}", currentFuncName(), {}

    sub_variables, sub_result_map = sub_result[3]
    if return_name in sub_result_map and sub_result_map[return_name][0]:
        data = sub_result_map[return_name][3]
    elif return_name in sub_variables:
        data = sub_variables[return_name]
    else:
        return False, f"script '{script_name}' return '{return_name}' not found in its data/variables", currentFuncName(), {}

    info = str(len(data)) if isinstance(data, list) else "1"
    return True, info, currentFuncName(), data


def run_script_command(command, data_map, current_state):
    """Выполнить вложенный сохранённый скрипт: GET script:<name>(params) AS result."""
    try:
        injected_variables = _type_script_params(command["parameters"], current_state)
        return _execute_script(command, injected_variables, current_state)
    except BaseException as e:
        error_message = f"run_script_command fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), {}


def run_apply_script_command(command, data_map, current_state):
    """APPLY поверх вызова скрипта: под-скрипт исполняется для каждой строки apply-данных.
    Параметры строки инъектируются в параметры вызова, результаты помечаются applied_* и склеиваются."""
    try:
        import pandas
        applyed_data = data_map[command['apply']['data']]
        if len(applyed_data) == 0:
            return True, "empty applyed data", currentFuncName(), []
        # проверяем, что применяемые столбцы есть в каждой строке
        for i, line in enumerate(applyed_data):
            for column in command['apply']['columns']:
                if column['column'] not in line:
                    return False, f"there is not column {column['column']} in {i} line of {command['apply']['data']}", currentFuncName(), []

        data = []
        for i, line in enumerate(applyed_data):
            # параметры строки -> инъекция в параметры вызова скрипта
            row_variables = {column["as"]: line[column['column']] for column in command['apply']['columns']}
            injection = process_injections(command["parameters"], row_variables, current_state)
            if not injection[0]:
                return False, f"apply var injection error: {injection[1]}", currentFuncName(), []
            injected_variables = _type_script_params(injection[3], current_state)

            shard_result = _execute_script(command, injected_variables, current_state)
            if not shard_result[0]:
                return False, f"apply script {i} iteration error: {shard_result[1]}", currentFuncName(), {}

            shard_data = shard_result[3]
            if not isinstance(shard_data, list):
                # скрипт вернул переменную (не таблицу) -> нормализуем в строку
                shard_data = [{"value": shard_data}]
            # помечаем applied_<as>
            for shard_line in shard_data:
                if isinstance(shard_line, dict):
                    for column in command['apply']['columns']:
                        shard_line[f"applied_{column["as"]}"] = line[column['column']]
            data = data + shard_data

        # дедубликация при необходимости
        if "unique" in command["apply"] and len(command["apply"]["unique"]) > 0:
            data = pandas.DataFrame(data).drop_duplicates(command["apply"]["unique"]).to_dict('records')

        return True, str(len(data)), currentFuncName(), data

    except BaseException as e:
        error_message = f"run_apply_script_command fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), {}