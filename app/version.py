"""Единый источник версии приложения для логирования и UI.

Порядок определения (первый успешный выигрывает):
  1. git-тег репозитория — `git describe --tags --abbrev=0` (ведущая 'v' отбрасывается).
     Это даёт актуальную версию автоматически при релизах через теги, без ручного бампа.
  2. файл `VERSION` в корне репозитория — для упакованных деплоев без .git
     (записывается на этапе сборки).
  3. фолбэк-константа.

Результат кешируется на процесс (определяется один раз при старте)."""

import os
import subprocess

_FALLBACK_VERSION = "0.0.0-dev"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cached_version = None


def _from_git():
    try:
        out = subprocess.run(
            ["git", "-C", _REPO_ROOT, "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            tag = out.stdout.strip()
            if tag:
                return tag[1:] if tag[:1] in ("v", "V") else tag
    except BaseException:
        pass
    return None


def _from_file():
    try:
        with open(os.path.join(_REPO_ROOT, "VERSION"), "r", encoding="utf-8") as handle:
            value = handle.read().strip()
            if value:
                return value[1:] if value[:1] in ("v", "V") else value
    except BaseException:
        pass
    return None


def get_app_version():
    """Актуальная версия приложения (git-тег -> файл VERSION -> фолбэк). Кешируется."""
    global _cached_version
    if _cached_version is None:
        _cached_version = _from_git() or _from_file() or _FALLBACK_VERSION
    return _cached_version
