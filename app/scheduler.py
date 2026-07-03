"""Планировщик запуска сохранённых DSL-скриптов по cron-расписанию.

- Минимальный 5-полевой cron-матчер (stdlib): min hour dom mon dow, с `*`, `a`, `a,b`, `a-b`, `*/n`, `a-b/n`.
- Запуск — в контексте владельца расписания (роли резолвятся на момент срабатывания; заблокирован -> skip).
- Пропущенные при простое срабатывания не догоняются. Время — локальное серверное.
- Крутится в ОДНОМ (web) процессе; overlap-guard не даёт параллельно повторить то же расписание.
"""

import time
import json
import uuid
import syslog
import datetime
import threading

from app.logging import get_log_message, logger_log, currentFuncName, currentTimestamp


# ── cron ────────────────────────────────────────────────────────────────────
def _parse_field(field, lo, hi):
    """Разобрать одно cron-поле в множество допустимых целых в [lo, hi]. Бросает ValueError на мусор."""
    field = field.strip()
    values = set()
    for part in field.split(","):
        part = part.strip()
        if part == "":
            raise ValueError("empty cron part")
        step = 1
        rng = part
        if "/" in part:
            rng, step_str = part.split("/", 1)
            step = int(step_str)
            if step <= 0:
                raise ValueError("cron step must be > 0")
        if rng == "*":
            start, end = lo, hi
        elif "-" in rng:
            a, b = rng.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(rng)
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron value out of range [{lo},{hi}]: {part}")
        values.update(range(start, end + 1, step))
    return values


def validate_cron(expr):
    """(ok, msg) — валидно ли 5-полевое cron-выражение."""
    try:
        fields = (expr or "").split()
        if len(fields) != 5:
            return False, "cron must have 5 fields: min hour dom mon dow"
        minute, hour, dom, month, dow = fields
        _parse_field(minute, 0, 59)
        _parse_field(hour, 0, 23)
        _parse_field(dom, 1, 31)
        _parse_field(month, 1, 12)
        _parse_field(dow, 0, 7)
        return True, "Ok"
    except BaseException as e:
        return False, f"invalid cron: {e}"


def cron_matches(expr, dt):
    """Совпадает ли момент dt (aware/naive datetime) с cron-выражением expr."""
    fields = (expr or "").split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    try:
        if dt.minute not in _parse_field(minute, 0, 59):
            return False
        if dt.hour not in _parse_field(hour, 0, 23):
            return False
        if dt.month not in _parse_field(month, 1, 12):
            return False
        doms = _parse_field(dom, 1, 31)
        dows = {(v % 7) for v in _parse_field(dow, 0, 7)}   # 7 и 0 -> воскресенье
    except BaseException:
        return False
    cron_dow = (dt.weekday() + 1) % 7   # weekday: Пн=0..Вс=6 -> cron: Вс=0..Сб=6
    dom_restricted = dom.strip() != "*"
    dow_restricted = dow.strip() != "*"
    dom_ok = dt.day in doms
    dow_ok = cron_dow in dows
    # стандартная семантика Vixie cron: если заданы ОБА (dom и dow) — совпадение по любому из них
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    if dom_restricted:
        return dom_ok
    if dow_restricted:
        return dow_ok
    return True


def next_run(expr, from_dt, horizon_minutes=366 * 24 * 60):
    """Следующий момент срабатывания после from_dt (для показа). None, если не найдено в горизонте."""
    probe = (from_dt.replace(second=0, microsecond=0)) + datetime.timedelta(minutes=1)
    for _ in range(horizon_minutes):
        if cron_matches(expr, probe):
            return probe
        probe += datetime.timedelta(minutes=1)
    return None


# ── исполнение ────────────────────────────────────────────────────────────────
_running = set()            # id расписаний, выполняющихся прямо сейчас (overlap-guard)
_running_lock = threading.Lock()
_scheduler_started = False


def _truthy(value):
    return value in (True, 1, "1", "true", "True", "t", "yes")


def _has_meaningful_data(data):
    """Есть ли в результате реальные данные (не [], не [{}], не список пустых dict)."""
    if not isinstance(data, list) or len(data) == 0:
        return False
    for row in data:
        if isinstance(row, dict):
            if len(row) > 0:
                return True
        elif row not in (None, ""):
            return True
    return False


def fire_schedule(schedule, base_state):
    """Выполнить один запуск расписания в контексте владельца и записать в историю (executions)."""
    from app.db import get_user_by_username, get_actual_object_by_name, create_execution, set_schedule_last_run
    from app.engine import command_parser
    from engine import commands_executor

    schedule_id = schedule["id"]
    owner = schedule["owner"]
    probe_state = {**base_state, "username": owner}

    # владелец: резолвим права на момент запуска; нет/заблокирован -> skip
    user_result = get_user_by_username(owner, probe_state)
    if not user_result[0] or not user_result[3].get("enabled", False):
        logger_log(syslog.LOG_WARNING, get_log_message(
            f"schedule '{schedule.get('name')}' skipped: owner '{owner}' missing or disabled",
            currentFuncName(), probe_state))
        set_schedule_last_run(schedule_id, currentTimestamp(), 0, probe_state)
        return

    roles = user_result[3].get("roles", []) or []
    state = {**base_state, "username": owner, "roles": roles,
             "user_session_id": str(uuid.uuid4()), "client_ip_address": "scheduler", "client_port": 0,
             "codemirror_theme": "monokai", "aggrid_theme": "ag-theme-balham-dark"}

    # загружаем сохранённый script-объект
    object_result = get_actual_object_by_name(schedule["script_name"], "('script')", state)
    if not object_result[0] or not object_result[3]:
        logger_log(syslog.LOG_ERR, get_log_message(
            f"schedule '{schedule.get('name')}': script object '{schedule['script_name']}' not found",
            currentFuncName(), state))
        set_schedule_last_run(schedule_id, currentTimestamp(), 0, state)
        return
    script_text = (object_result[3].get("json") or {}).get("script")
    if not script_text:
        logger_log(syslog.LOG_ERR, get_log_message(
            f"schedule '{schedule.get('name')}': script '{schedule['script_name']}' has no body",
            currentFuncName(), state))
        set_schedule_last_run(schedule_id, currentTimestamp(), 0, state)
        return

    fired_ts = currentTimestamp()
    started = time.monotonic()
    ok = False
    message = ""
    executor_result = None
    try:
        parsed = command_parser(script_text, state)
        executor_result = commands_executor(parsed, state)
        ok = bool(executor_result[0])
        message = executor_result[1]
    except BaseException as run_error:
        message = f"scheduled run crashed: {run_error}"
        logger_log(syslog.LOG_ERR, get_log_message(message, currentFuncName(), state))

    # ALERT с результатом: если скрипт вернул НЕпустые данные (по полю `return` объекта) —
    # логируем время запуска, имя скрипта и сами данные (для мониторинга зашедуленных прогонов)
    if ok and isinstance(executor_result[3], tuple):
        variables, result_map = executor_result[3]
        return_name = (object_result[3].get("json") or {}).get("return")
        result_data = None
        result_source = return_name
        # 1) явный return скрипта
        if return_name:
            if return_name in result_map and result_map[return_name][0]:
                result_data = result_map[return_name][3]
            elif return_name in variables:
                result_data = variables[return_name]
        # 2) фолбэк: если return не задан/пуст — последняя произведённая непустая таблица (факт. вывод)
        if not _has_meaningful_data(result_data):
            for key in reversed(list(result_map.keys())):
                entry = result_map[key]
                if entry and entry[0] and _has_meaningful_data(entry[3]):
                    result_data, result_source = entry[3], key
                    break
        if _has_meaningful_data(result_data):
            payload = json.dumps(result_data, ensure_ascii=False, default=str)
            if len(payload) > 20000:
                payload = payload[:20000] + "…(truncated)"
            logger_log(syslog.LOG_ALERT, get_log_message(
                f"scheduled script result | time={fired_ts} | "
                f"script='{schedule.get('name')}' ({schedule['script_name']}) | "
                f"table='{result_source}' | data={payload}",
                currentFuncName(), state))

    create_execution(str(uuid.uuid4()), owner, 1 if ok else 0, {
        "script": script_text,
        "duration_seconds": round(time.monotonic() - started, 3),
        "agent": False,
        "scheduled": True,
        "schedule_id": schedule_id,
        "schedule_name": schedule.get("name"),
        "message": message,
    }, state)
    set_schedule_last_run(schedule_id, currentTimestamp(), 1 if ok else 0, state)
    logger_log(syslog.LOG_INFO, get_log_message(
        f"schedule '{schedule.get('name')}' fired (owner={owner}, ok={ok})", currentFuncName(), state))


def _run_guarded(schedule, base_state):
    try:
        fire_schedule(schedule, base_state)
    finally:
        with _running_lock:
            _running.discard(schedule["id"])


def run_schedule_now(schedule_id, base_state):
    """Немедленный запуск расписания (кнопка «Запустить сейчас»). (ok, msg)."""
    from app.db import get_schedule
    get_result = get_schedule(schedule_id, base_state)
    if not get_result[0] or not get_result[3]:
        return False, (get_result[1] if not get_result[0] else "schedule not found")
    schedule = get_result[3]
    with _running_lock:
        if schedule_id in _running:
            return False, "already running"
        _running.add(schedule_id)
    threading.Thread(target=_run_guarded, args=(schedule, base_state), daemon=True).start()
    return True, "started"


def _tick(base_state, now):
    """Одна проверка минуты: запустить все подошедшие enabled-расписания (без догона пропусков)."""
    from app.db import list_schedules
    list_result = list_schedules(base_state)
    if not list_result[0]:
        return
    now_minute = now.strftime("%Y-%m-%dT%H:%M")
    for schedule in list_result[3]:
        if not _truthy(schedule.get("enabled")):
            continue
        if not cron_matches(schedule.get("cron") or "", now):
            continue
        if (schedule.get("last_run") or "")[:16] == now_minute:   # уже сработало в эту минуту
            continue
        with _running_lock:
            if schedule["id"] in _running:
                continue
            _running.add(schedule["id"])
        threading.Thread(target=_run_guarded, args=(schedule, base_state), daemon=True).start()


def run_scheduler(base_state):
    """Фоновый цикл планировщика (daemon). Проверяет расписания раз в минуту. Идемпотентен по старту."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    logger_log(syslog.LOG_INFO, get_log_message("scheduler started", currentFuncName(), base_state))
    while True:
        try:
            _tick(base_state, datetime.datetime.now().astimezone())
        except BaseException as tick_error:
            logger_log(syslog.LOG_ERR, get_log_message(f"scheduler tick fail: {tick_error}", currentFuncName(), base_state))
        # спим до начала следующей минуты
        now = datetime.datetime.now().astimezone()
        time.sleep(max(1.0, 60.0 - now.second - now.microsecond / 1_000_000.0))
