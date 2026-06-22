
import syslog
import ipaddress
import json
import re
from app.logging import get_log_message, logger_log, currentFuncName
from app.db import get_user_by_username, get_access_networks
from typing import Tuple, List, Dict, Optional

REGEX_PASSWORD_RULE = r"^(?=.*[0-9])(?=.*[0-9])(?=.*[!@#$%^&*:.>\/<,;+?~–}{)(\]\[])(?=.*[a-z])(?=.*[A-Z])[0-9a-zA-Z!@#$%^&*:.>\/<,;+?~–}{)(\]\[]{17,}$"
REGEX_USERNAME_RULE = r"^[0-9a-zA-Z._-]{3,}$"
REGEX_ITEMNAME_RULE = r"^[0-9a-zA-Z._\]\[\s/-]{3,}$"
REGEX_COMMENT_RULE = r"^[:/0-9a-zA-Zа-яА-Я.\s_-]*$"

def check_regex_rule(password, password_rule):
    search = re.search(password_rule, password)
    if search:
        return True
    else:
        return False
    
def json_validate(text):
    try:
        json.loads(text)
        return True
    except BaseException as e:
        return False

def command_validator(commands:list,current_state:dict):
    # тут реализованы всевозможные проверки
    for command in commands:
        match command["command"]:
            case "DEF":
                pass
            case "GET":
                pass
            case "CALC":
                pass
            case "PRINT":
                pass
            case "SAVE":
                pass
            case "SHOW":
                pass
            case "NOTIFY":
                pass
    return True, "OK", currentFuncName(), None

def validate_itemname(itemname: str, current_state: dict) -> Tuple[bool, str, str, None]:
    try:
        if check_regex_rule(itemname, REGEX_ITEMNAME_RULE) == False:
            error_message = f"wrong object name (must {REGEX_ITEMNAME_RULE})"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        return True, "OK", currentFuncName(), None
    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None

def validate_comment(comment: str, current_state: dict) -> Tuple[bool, str, str, None]:
    try:
        if check_regex_rule(comment, REGEX_COMMENT_RULE) == False:
            error_message = f"wrong comment (must {REGEX_COMMENT_RULE})"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        return True, "OK", currentFuncName(), None
    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None
        
def raw_login_validation(raw_login: str, current_state: dict):
    try:
        if check_regex_rule(raw_login, REGEX_USERNAME_RULE) == False:
            error_message = f"wrong raw login"
            logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        return True, "OK", currentFuncName(), None
    except BaseException as e:
        error_message = f"fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(error_message, currentFuncName(), current_state))

def check_ip_in_whitelist(from_db_access_networks, address, current_state):
    try:
        for network in from_db_access_networks:
            if network["allow"] != 0:
                if ipaddress.ip_address(address) in ipaddress.ip_network(network["cidr"]):
                    return True
        return False
    except BaseException as e:
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {str(e)}", currentFuncName(), current_state))
        return False
    
def check_current_user_status(current_state):
    current_user_is_active = False
    current_users_roles = []
    current_user_with_allowed_ip = False
    # получаем данные по пользователю из БД
    from_db_user = get_user_by_username(current_state["username"], current_state)
    # данные из БД по пользователю были получены корректно?
    if from_db_user[0] == False:
        logger_log(syslog.LOG_ERR, get_log_message("db_get_user_result data is unavailable", currentFuncName(), current_state))
        return False, current_users_roles, current_user_with_allowed_ip, from_db_user
    
    current_user = from_db_user[3] # ["enabled", "name", "pass", "roles", "json"]
    # пользователь не заблокирован?
    if not current_user["enabled"]:
        logger_log(syslog.LOG_ALERT, get_log_message("disabled account working attempt", currentFuncName(), current_state))
        return current_user_is_active, current_users_roles, current_user_with_allowed_ip, current_user
    # пользователь активен
    current_user_is_active = True
    # забираем роли пользователя
    current_users_roles = current_user["roles"]

    # получаем разрешённые сети из БД
    from_db_access_networks = get_access_networks(current_state)
    # данные из БД по разрешённым адресам получены корректно?
    if from_db_access_networks[0] == False:
        logger_log(syslog.LOG_ERR, get_log_message(f"from_db_access_networks error: {from_db_access_networks[1]}", currentFuncName(), current_state))
        return current_user_is_active, current_users_roles, current_user_with_allowed_ip, from_db_user
    # проверяем адрес пользователя на вхождение в разрешённые сети
    if check_ip_in_whitelist(from_db_access_networks[3], current_state["client_ip_address"], current_state) == False:
        logger_log(syslog.LOG_ERR, get_log_message(f"client address {current_state["client_ip_address"]} is not in access networks", currentFuncName(), current_state))
        return current_user_is_active, current_users_roles, current_user_with_allowed_ip, from_db_user
    # пользователь работает с разрешённого ip
    current_user_with_allowed_ip = True
    
    return current_user_is_active, current_users_roles, current_user_with_allowed_ip, current_user