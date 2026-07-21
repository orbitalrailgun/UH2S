"""Реестр одноразовых файловых загрузок для больших SAVE-экспортов.

Большие экспорты собираются в temp-файл на диске (не в RAM) и отдаются потоком через роут
/download/{token} (см. front.py), а не байтами через websocket NiceGUI — иначе на гигабайтах
рвётся соединение (браузер ловит nomodule-заглушку) и результат теряется.

Безопасность: token = secrets.token_urlsafe (неугадываемый, capability-URL, как подписанная ссылка);
доступ one-shot (после отдачи запись удаляется); путь всегда генерируется сервером во временном каталоге
(нет пользовательского ввода в путь -> нет path traversal); незабранные файлы подчищаются по TTL.
"""
import os
import secrets
import tempfile
import threading
import time

# TTL для незабранных файлов (сек): если клиент так и не скачал — файл удалится при следующей регистрации.
DOWNLOAD_TTL_SECONDS = 3600

# каталог для temp-экспортов: UH2S_EXPORT_DIR или системный tempdir
_EXPORT_DIR = os.environ.get("UH2S_EXPORT_DIR") or tempfile.gettempdir()

_lock = threading.Lock()
# token -> (path, filename, media_type, created_monotonic)
_registry = {}


def export_tempfile(suffix=""):
    """Создать пустой temp-файл в каталоге экспортов и вернуть его путь (дескриптор сразу закрываем —
    писать в файл будут по пути). Расширение suffix помогает диагностике на диске."""
    os.makedirs(_EXPORT_DIR, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="uh2s_export_", suffix=suffix, dir=_EXPORT_DIR)
    os.close(fd)
    return path


def _remove_quietly(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _sweep_expired(now):
    """Удалить с диска и из реестра записи старше TTL (незабранные)."""
    for token in [t for t, v in _registry.items() if now - v[3] > DOWNLOAD_TTL_SECONDS]:
        entry = _registry.pop(token, None)
        if entry:
            _remove_quietly(entry[0])


def register_download(path, filename, media_type=""):
    """Зарегистрировать готовый temp-файл для одноразовой отдачи. Возвращает случайный token
    (используется в URL /download/{token}). Заодно подчищает протухшие записи."""
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        _sweep_expired(now)
        _registry[token] = (path, filename, media_type, now)
    return token


def consume_download(token):
    """Достать и УДАЛИТЬ запись по token (one-shot). Возвращает (path, filename, media_type)
    или None, если токена нет/протух. Сам файл удаляет вызывающий после отдачи."""
    with _lock:
        entry = _registry.pop(token, None)
    if entry is None:
        return None
    path, filename, media_type, _created = entry
    return path, filename, media_type
