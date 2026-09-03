"""Реестр файловых загрузок для больших SAVE-экспортов и файлов хранилища.

Большие экспорты собираются в temp-файл на диске (не в RAM) и отдаются потоком через роут
/download/{token} (см. front.py), а не байтами через websocket NiceGUI — иначе на гигабайтах
рвётся соединение (браузер ловит nomodule-заглушку) и результат теряется.

Безопасность: token = secrets.token_urlsafe (неугадываемый, capability-URL, как подписанная ссылка);
доступ по умолчанию one-shot (режим export; см. MODE_* ниже); путь всегда генерируется сервером
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

# режимы владения файлом:
#   export   — одноразовый экспорт: удаляется сразу после отдачи (SAVE с одним файлом);
#   reusable — ссылка живёт до TTL и переиспользуется (несколько SAVE в одном прогоне: файл нужен
#              повторно, потому что скачивание инициирует пользователь кнопкой), файл убирает sweep;
#   external — файл принадлежит другой подсистеме (файловые записи хранилища, app/storage_files.py):
#              этот модуль его НЕ удаляет ни после отдачи, ни по TTL.
MODE_EXPORT = "export"
MODE_REUSABLE = "reusable"
MODE_EXTERNAL = "external"


_lock = threading.Lock()
# token -> (path, filename, media_type, created_monotonic, mode)
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
    """Убрать из реестра записи старше TTL; файл удаляется у export/reusable, но не у external.

    external — файлы хранилища: у них истекает только ссылка, сам файл принадлежит storage_files,
    иначе через час после первого скачивания данные пропали бы, а запись в БД осталась."""
    for token in [t for t, v in _registry.items() if now - v[3] > DOWNLOAD_TTL_SECONDS]:
        entry = _registry.pop(token, None)
        if entry and entry[4] != MODE_EXTERNAL:
            _remove_quietly(entry[0])


def register_download(path, filename, media_type="", mode=MODE_EXPORT):
    """Зарегистрировать готовый файл для отдачи. Возвращает случайный token
    (используется в URL /download/{token}). Заодно подчищает протухшие записи.

    mode — export | reusable | external (см. константы выше)."""
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        _sweep_expired(now)
        _registry[token] = (path, filename, media_type, now, str(mode))
    return token


def consume_download(token):
    """Достать запись по token. Возвращает (path, filename, media_type, delete_after) или None.

    Режим export — запись одноразовая (убирается из реестра), файл удаляет вызывающий после отдачи.
    Режимы reusable/external — запись остаётся до TTL (ссылку можно нажать повторно), файл не удаляется."""
    with _lock:
        entry = _registry.get(token)
        if entry is not None and entry[4] == MODE_EXPORT:
            _registry.pop(token, None)
    if entry is None:
        return None
    path, filename, media_type, _created, mode = entry
    return path, filename, media_type, (mode == MODE_EXPORT)
