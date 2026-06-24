"""Простая мультиязычность (i18n) для Universal Harvester.

Добавление нового языка:
  1) добавьте код языка в SUPPORTED_LANGUAGES (код -> отображаемое имя);
  2) добавьте словарь переводов в TRANSLATIONS[<код>] (ключи как в "en").
Отсутствующие ключи берутся из языка по умолчанию (en), затем — сам ключ (не падаем).

Использование в UI: переводимая строка резолвится через translate(key, lang).
Язык запроса резолвится resolve_language(saved, accept_language_header)."""

DEFAULT_LANGUAGE = "en"

# код языка -> человекочитаемое имя (порядок = порядок в выпадающем списке)
SUPPORTED_LANGUAGES = {
    "en": "English",
    "ru": "Русский",
}

TRANSLATIONS = {
    "en": {
        # навигация
        "nav.settings": "Settings",
        "nav.secrets": "Secrets",
        "nav.objects": "Objects",
        "nav.ai": "AI",
        "nav.harvester": "Harvester",
        "nav.history": "History",
        "nav.logout": "Logout",
        "nav.settings.tip": "Settings",
        "nav.secrets.tip": "Secrets storage",
        "nav.objects.tip": "Saved objects",
        "nav.ai.tip": "LLM chat for fetching and processing data",
        "nav.harvester.tip": "Run scripts",
        "nav.history.tip": "Run history",
        "nav.logout.tip": "Log out",
        # Settings: язык
        "settings.language.title": "Language",
        "settings.language.label": "Interface language",
        "settings.language.apply": "Apply",
        "settings.language.saved": "Language saved",
        "settings.language.hint": "The language is applied after the page reloads and is saved for your account.",
        # Settings: заголовки разделов
        "settings.section.appearance": "Appearance",
        "settings.section.account": "Account",
        "settings.section.users": "User management (admin)",
        "settings.section.ai": "AI agent: settings and log (admin)",
        "settings.section.networks": "Allowed networks (IP access)",
        "settings.section.apikeys": "API keys",
        "settings.error": "Settings error: {error}",
    },
    "ru": {
        # навигация
        "nav.settings": "Настройки",
        "nav.secrets": "Секреты",
        "nav.objects": "Объекты",
        "nav.ai": "AI",
        "nav.harvester": "Harvester",
        "nav.history": "История",
        "nav.logout": "Выход",
        "nav.settings.tip": "Настройки",
        "nav.secrets.tip": "Хранилище секретов",
        "nav.objects.tip": "Сохранённые объекты",
        "nav.ai.tip": "LLM чат получения и обработки данных",
        "nav.harvester.tip": "Исполнение скриптов",
        "nav.history.tip": "История запусков",
        "nav.logout.tip": "Выход",
        # Settings: язык
        "settings.language.title": "Язык",
        "settings.language.label": "Язык интерфейса",
        "settings.language.apply": "Применить",
        "settings.language.saved": "Язык сохранён",
        "settings.language.hint": "Язык применяется после перезагрузки страницы и сохраняется за вашей учётной записью.",
        # Settings: заголовки разделов
        "settings.section.appearance": "Внешний вид",
        "settings.section.account": "Учётная запись",
        "settings.section.users": "Управление пользователями (админ)",
        "settings.section.ai": "AI-агент: настройки и журнал (админ)",
        "settings.section.networks": "Разрешённые сети (доступ по IP)",
        "settings.section.apikeys": "API-ключи",
        "settings.error": "Ошибка раздела Settings: {error}",
    },
}


def translate(key, lang=DEFAULT_LANGUAGE, **kwargs):
    """Перевод ключа на язык lang. Фолбэк: язык по умолчанию -> сам ключ. Поддерживает .format(**kwargs)."""
    catalog = TRANSLATIONS.get(lang) or {}
    text = catalog.get(key)
    if text is None:
        text = (TRANSLATIONS.get(DEFAULT_LANGUAGE) or {}).get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except BaseException:
            return text
    return text


def parse_accept_language(header):
    """Список кодов языков из заголовка Accept-Language (без q-весов, в порядке предпочтения)."""
    languages = []
    for part in (header or "").split(","):
        code = part.split(";")[0].strip().lower()
        if not code:
            continue
        primary = code.split("-")[0]
        if primary not in languages:
            languages.append(primary)
    return languages


def resolve_language(saved, accept_language_header=""):
    """Итоговый язык: сохранённый (если поддерживается) -> язык браузера (если поддерживается) -> по умолчанию."""
    if saved and saved in SUPPORTED_LANGUAGES:
        return saved
    for code in parse_accept_language(accept_language_header):
        if code in SUPPORTED_LANGUAGES:
            return code
    return DEFAULT_LANGUAGE
