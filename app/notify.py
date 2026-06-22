import requests
import syslog
from app.logging import currentTimestamp, get_log_message, logger_log, currentFuncName
from app.db import get_secret



def send_mattermost_notify(mattermost_host, api_key, target_username, message_text, current_state):
    try:
        ###############################################################
        # Сначала надо получить собственный user id бота в mattermost
        ###############################################################
        response = requests.get("https://"+mattermost_host+"/api/v4/users/me",headers = {'Authorization': f"Bearer {api_key}"})
        if response.status_code < 200 or response.status_code >=300:
            # ошибка, не можем получить себя
            error_message = f"Cannot get bot mattermost account for {mattermost_host}"
            logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
        bot_id = response.json()["id"]

        ###############################################################
        # Получаем пользователя по его юзернейму (id кому отправляем уведомление)
        ###############################################################
        response = requests.post("https://"+mattermost_host+"/api/v4/users/usernames",headers = {'Authorization': f"Bearer {api_key}",'Content-type': 'content_type_value'}, json = [target_username])
        if response.status_code < 200 or response.status_code >=300:
            # ошибка, не можем получить пользователя
            error_message = f"Cannot get target user mattermost account {target_username} for server {mattermost_host}"
            logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
        target_user_id = ""
    
        for responsed_user in response.json():
            if responsed_user["username"] == target_username:
                target_user_id = responsed_user["id"]
    
        if target_user_id == "":
            # ошибка, получен не тот пользователь
            error_message = f"Mattermost server username is not equal config username {target_username}"
            logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
        ###############################################################
        # создаём приватный канал бот<->пользователь
        ###############################################################
        response = requests.post("https://"+mattermost_host+"/api/v4/channels/direct",headers = {'Authorization': f"Bearer {api_key}",'Content-type': 'content_type_value'}, json = [bot_id, target_user_id])
        if response.status_code < 200 or response.status_code >=300:
            # ошибка создания канала
            error_message = f"Cannot create private bot<->user channel"
            logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
    
        channel_id = response.json()["id"]
    
        # 4. отправка сообщения
        response = requests.post("https://"+mattermost_host+"/api/v4/posts",headers = {'Authorization': f"Bearer {api_key}",'Content-type': 'content_type_value'}, json = {"channel_id":channel_id,"message":message_text})
        if response.status_code < 200 or response.status_code >=300:
            # ошибка отправки
            error_message = f"Cannot send message to user {target_username}"
            logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
    except BaseException as e:
        error_message = f"Generic exeption: {e}"
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None
    return True, "OK", currentFuncName(), None

#def notify_mattermost_proc(user_mattermost_json_node, notify_text, current_state):
def notify_mattermost_proc(notifier_object, notifier_user_conf, message, current_state):
    #command["notifier_object"], command["notifier_user_conf"], command["message"], current_state

    if "enable" not in notifier_user_conf:
        logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации mattermost: отсутствует флаг enable", currentFuncName(), current_state))
        return
    if notifier_user_conf["enable"]:
        if "server" not in notifier_object:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации mattermost: отсутствует строка server", currentFuncName(), current_state))
            return
        if "username" not in notifier_user_conf:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации mattermost: отсутствует строка username", currentFuncName(), current_state))
            return
        if "key" not in notifier_object:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации mattermost: отсутствует нода key", currentFuncName(), current_state))
            return
        if "system" not in notifier_object["key"]:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации mattermost: отсутствует нода key->system", currentFuncName(), current_state))
            return
        if "account" not in notifier_object["key"]:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации mattermost: отсутствует нода key->account", currentFuncName(), current_state))
            return
        get_key_result = get_secret(notifier_object["key"]["system"], notifier_object["key"]["account"], current_state)
        if get_key_result[0] == False:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка получения ключей для нотификации mattermost: {get_key_result[1]}", currentFuncName(), current_state))
            return
        key = get_key_result[3]

        send_mattermost_notify_result = send_mattermost_notify(
                            notifier_object["server"], 
                            key, 
                            notifier_user_conf["username"], 
                            message, 
                            current_state)
        if send_mattermost_notify_result[0] == False:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации mattermost: {send_mattermost_notify_result[1]}", currentFuncName(), current_state))
            return
        
def send_telegram_notify(bot_token, chat_id, message_text, current_state):
    try:
        # блок для получения chat_id
        # блок для получения для внесения нового chat_id можно воспользоваться данной функцией
        # пусть сначала пользователь напишет боту
        # url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        # print(requests.get(url).json())

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&text={message_text}"
        response = requests.get(url)#.json()

        if response.status_code < 200 or response.status_code >=300:
            # ошибка, не можем получить себя
            error_message = f"Cannot GET to telegram server"
            logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
        response_json = response.json()
        
        if "ok" not in response_json:
            error_message = f"ok node is not in telegram response json"
            logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None
        
        if response_json["ok"] != True:
            error_message = f"ok is not true in telegram response json"
            logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
            return False, error_message, currentFuncName(), None

    except BaseException as e:
        error_message = f"Generic exeption: {e}"
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), None
    return True, "OK", currentFuncName(), None

#def notify_telegram_proc(user_telegram_json_node, notify_text, current_state):
def notify_telegram_proc(notifier_object, notifier_user_conf, message, current_state):
    #command["notifier_object"], command["notifier_user_conf"], command["message"], current_state
    if "enable" not in notifier_user_conf:
        logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации telegram: отсутствует флаг enable", currentFuncName(), current_state))
        return
    if notifier_user_conf["enable"]:
        if "chat_id" not in notifier_user_conf:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации telegram: отсутствует строка chat_id", currentFuncName(), current_state))
            return
        if "key" not in notifier_object:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации telegram: отсутствует нода key", currentFuncName(), current_state))
            return
        if "system" not in notifier_object["key"]:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации telegram: отсутствует нода key->system", currentFuncName(), current_state))
            return
        if "account" not in notifier_object["key"]:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации telegram: отсутствует нода key->account", currentFuncName(), current_state))
            return
        get_key_result = get_secret(notifier_object["key"]["system"], notifier_object["key"]["account"], current_state)
        if get_key_result[0] == False:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка получения ключей для нотификации telegram: {get_key_result[1]}", currentFuncName(), current_state))
            return
        key = get_key_result[3]

        send_telegram_notify_result = send_telegram_notify(
                            key, 
                            notifier_user_conf["chat_id"], 
                            message, 
                            current_state)
        if send_telegram_notify_result[0] == False:
            logger_log(syslog.LOG_ERR, get_log_message(f"Ошибка нотификации telegram: {send_telegram_notify_result[1]}", currentFuncName(), current_state))
            return