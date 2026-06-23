import syslog
from app.logging import get_log_message, logger_log, currentFuncName
from app.db import get_secret

# Объект типа llm (раздел Objects):
# {
#   "type": "ollama" | "openai",          # провайдер: Ollama API или OpenAI-совместимый
#   "url": "...",                          # см. ниже про точность url
#   "model": "llama3.2",                   # имя модели
#   "request_timeout": 60,                 # таймаут запроса, сек
#   "verify": true,                        # проверять TLS
#   "key": {"system": "...", "account": "..."}  # опц.: секрет -> Bearer (нужен для openai)
# }
#
# ВАЖНО про url:
#   ollama -> базовый URL без путей, напр. "http://host:11434"
#             (функции обращаются к {url}/api/tags, {url}/api/chat)
#   openai -> URL ДОЛЖЕН включать /v1, напр. "https://foundation-models.api.cloud.ru/v1"
#             (функции обращаются к {url}/models, {url}/chat/completions — без добавления /v1)


def _llm_resolve_key(llm_json, current_state):
    """Достать токен из секрета по llm_json['key'] = {system, account}; иначе пустая строка."""
    key = llm_json.get("key")
    if isinstance(key, dict) and "system" in key and "account" in key:
        get_secret_result = get_secret(key["system"], key["account"], current_state)
        if get_secret_result[0]:
            return get_secret_result[3]
    return ""


def _llm_headers(llm_json, current_state):
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f'{current_state.get("app_name", "UH")}/{current_state.get("app_version", "0")}',
    }
    token = _llm_resolve_key(llm_json, current_state)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def llm_health_check(llm_json, current_state):
    """Проверка готовности LLM-объекта. Возврат (ok: bool, message: str).

    ollama  -> GET {url}/api/tags  (+ наличие модели);
    openai  -> GET {url}/v1/models (Bearer из key)."""
    import requests
    try:
        provider = (llm_json.get("type") or "ollama").strip().lower()
        url = (llm_json.get("url") or "").rstrip("/")
        model = llm_json.get("model", "")
        timeout = llm_json.get("request_timeout", 30)
        verify = llm_json.get("verify", True)
        headers = _llm_headers(llm_json, current_state)

        if not url:
            return False, "в объекте llm не задан url"

        if provider == "ollama":
            response = requests.get(f"{url}/api/tags", headers=headers, verify=verify, timeout=timeout)
            if response.status_code != 200:
                return False, f"ollama /api/tags http {response.status_code}"
            available = [m.get("name") or m.get("model") for m in response.json().get("models", [])]
            available = [m for m in available if m]
            if model and model not in available and not any(model in m for m in available):
                shown = ", ".join(available[:10]) if available else "—"
                return False, f"ollama доступна, но модель '{model}' не найдена (есть: {shown})"
            return True, f"ollama готова, модель '{model}'"

        if provider in ("openai", "openai_compatible"):
            # url уже включает /v1 -> обращаемся к {url}/models
            response = requests.get(f"{url}/models", headers=headers, verify=verify, timeout=timeout)
            if response.status_code != 200:
                return False, f"openai {url}/models http {response.status_code} ({response.text[:200]})"
            data = response.json().get("data", [])
            ids = [m.get("id") for m in data] if isinstance(data, list) else []
            if model and ids and model not in ids:
                return False, f"openai доступна, но модель '{model}' не в списке"
            return True, f"openai-совместимый сервер готов, модель '{model}'"

        return False, f"неизвестный тип llm '{provider}' (ollama | openai)"

    except Exception as e:
        error_message = f"health check fail: {str(e)}"
        logger_log(syslog.LOG_ERR, get_log_message(f"{error_message}", currentFuncName(), current_state))
        return False, error_message
