import syslog
from app.logging import get_log_message, logger_log, currentFuncName
from typing import Tuple, List, Dict, Optional

def encrypt(text_data, current_state: Dict):
    try:
        from cryptography.fernet import Fernet
        logger_log(syslog.LOG_DEBUG, get_log_message(f"start", currentFuncName(), current_state))
        cryptography = Fernet(str.encode(current_state["master_key"]))
        crypted = cryptography.encrypt(str.encode(text_data))
        logger_log(syslog.LOG_DEBUG, get_log_message(f"done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), crypted.decode("utf-8")
    except BaseException as e:
        error_message = f"{str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), ""


def decrypt(text_crypted, current_state: Dict):
    try:
        from cryptography.fernet import Fernet
        logger_log(syslog.LOG_DEBUG, get_log_message(f"start", currentFuncName(), current_state))
        cryptography = Fernet(str.encode(current_state["master_key"]))
        decrypted = cryptography.decrypt(str.encode(text_crypted)).decode("utf-8")
        logger_log(syslog.LOG_DEBUG, get_log_message(f"done", currentFuncName(), current_state))
        return True, "OK", currentFuncName(), decrypted
    except BaseException as e:
        error_message = f"{str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"fail: {error_message}", currentFuncName(), current_state))
        return False, error_message, currentFuncName(), ""
