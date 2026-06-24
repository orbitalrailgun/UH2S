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
        "nav.settings": "Settings", "nav.secrets": "Secrets", "nav.objects": "Objects",
        "nav.ai": "AI", "nav.harvester": "Harvester", "nav.history": "History", "nav.logout": "Logout",
        "nav.settings.tip": "Settings", "nav.secrets.tip": "Secrets storage", "nav.objects.tip": "Saved objects",
        "nav.ai.tip": "LLM chat for fetching and processing data", "nav.harvester.tip": "Run scripts",
        "nav.history.tip": "Run history", "nav.logout.tip": "Log out",
        # Settings: язык
        "settings.language.title": "Language", "settings.language.label": "Interface language",
        "settings.language.apply": "Apply", "settings.language.saved": "Language saved",
        "settings.language.hint": "The language is applied after the page reloads and is saved for your account.",
        # заголовки разделов
        "settings.section.appearance": "Appearance", "settings.section.account": "Account",
        "settings.section.users": "User management (admin)", "settings.section.ai": "AI agent: settings and log (admin)",
        "settings.section.networks": "Allowed networks (IP access)", "settings.section.apikeys": "API keys",
        "settings.error": "Settings error: {error}",
        # общие
        "settings.common.error": "Error: {error}", "settings.common.yes": "yes", "settings.common.no": "no",
        "settings.common.comment": "Comment", "settings.col.comment": "Comment",
        "settings.btn.save": "Save", "settings.btn.reset": "Reset", "settings.btn.cancel": "Cancel",
        "settings.btn.create": "Create", "settings.btn.refresh": "Refresh list", "settings.btn.close": "Close",
        # внешний вид
        "settings.appearance.hint": "Personal appearance settings. Applied immediately and saved for your account.",
        "settings.theme.label": "Theme", "settings.theme.dark": "Dark", "settings.theme.light": "Light",
        "settings.appearance.interface": "Interface", "settings.appearance.tables": "Tables and text blocks",
        "settings.appearance.codemirror": "Code editor (CodeMirror)", "settings.appearance.colors": "Interface colors",
        "settings.font.interface": "Interface font", "settings.font.tables": "Tables font", "settings.font.size": "Size, px",
        "settings.codemirror.hint": "The editor theme is set separately for dark and light themes.",
        "settings.codemirror.label": "Editor theme",
        "settings.colors.hint": "Colors are set separately for dark and light themes — the currently selected theme is edited.",
        "settings.color.bg": "Background", "settings.color.text": "Text", "settings.color.accent": "Accent",
        "settings.color.card": "Cards", "settings.color.title": "Titles", "settings.color.panel": "Panels",
        "settings.appearance.save_fail": "Failed to save: {error}", "settings.appearance.saved": "Appearance saved",
        # учётная запись
        "settings.account.user": "User: **{name}**", "settings.account.changepw": "Change password",
        "settings.account.pwrule": "New password: at least 17 characters, lower and upper case letters, a digit and a special character.",
        "settings.account.pwwarn": "⚠️ After changing the password you will be logged out of all your sessions — you will need to sign in again.",
        "settings.account.oldpw": "Current password", "settings.account.newpw": "New password",
        "settings.account.confirmpw": "Repeat new password", "settings.account.fill": "Fill in the current and new password",
        "settings.account.mismatch": "New password and its repeat do not match",
        "settings.pw.weak": "The new password does not meet the complexity requirements",
        "settings.account.userfail": "Failed to fetch user: {error}", "settings.account.wrongpw": "Current password is incorrect",
        "settings.account.confirm": "Change the password? You will be logged out of all your sessions.",
        "settings.account.confirm_yes": "Change and log out", "settings.account.changefail": "Failed to change password: {error}",
        "settings.account.changed": "Password changed. Logging out…", "settings.account.changebtn": "Change password",
        "settings.account.meta": "My metadata (JSON)",
        "settings.account.meta_hint": "Arbitrary data of your account (e.g. notification settings for NOTIFY).",
        "settings.meta.invalid_json": "Metadata must be valid JSON", "settings.meta.not_object": "Metadata must be a JSON object ({...})",
        "settings.meta.save_fail": "Failed to save metadata: {error}", "settings.meta.saved": "Metadata saved",
        "settings.meta.savebtn": "Save metadata",
        # пользователи (админ)
        "settings.users.hint": "Filter by columns (username, roles, metadata). Resetting the password and blocking immediately end all the user's sessions.",
        "settings.users.actions": "Actions for the selected user", "settings.users.none": "No user selected",
        "settings.users.roles": "Roles (JSON array)", "settings.users.resetpw": "New password (reset)",
        "settings.users.meta": "User metadata (JSON)",
        "settings.users.col.username": "Username", "settings.users.col.enabled": "Active",
        "settings.users.col.roles": "Roles", "settings.users.col.metadata": "Metadata",
        "settings.users.list_fail": "User list error: {error}",
        "settings.users.selected": "User: {name} ({status})", "settings.users.active": "active", "settings.users.blocked": "blocked",
        "settings.users.block": "Block", "settings.users.unblock": "Unblock", "settings.users.pick": "Select a user in the table",
        "settings.users.noselfblock": "You cannot block your own account",
        "settings.users.unblocked": "Unblocked: {name}", "settings.users.blocked_done": "Blocked: {name} (sessions ended)",
        "settings.roles.array": "Roles must be a JSON array", "settings.roles.array_strings": "Roles must be a JSON array (list of strings)",
        "settings.users.roles_saved": "Roles updated: {name}", "settings.users.pw_reset": "Password reset for {name} (sessions ended)",
        "settings.users.meta_saved": "Metadata saved: {name}",
        "settings.users.save_roles": "Save roles", "settings.users.reset_pw_btn": "Reset password",
        "settings.users.create": "Create user", "settings.users.username": "Username", "settings.users.password": "Password",
        "settings.users.name_rule": "Name: at least 3 characters, latin/digits/._-", "settings.users.created": "User created: {name}",
        # AI
        "settings.ai.limits": "Limits", "settings.ai.maxiter": "Max agent actions per session",
        "settings.ai.limits_saved": "AI limits saved", "settings.ai.save_limits": "Save limits",
        "settings.ai.log": "AI log — token consumption",
        "settings.ai.log_hint": "Per-user summary and request details (column filters: user, model, time).",
        "settings.ai.detail": "Request details", "settings.ai.log_fail": "AI log error: {error}",
        "settings.ai.col.user": "User", "settings.ai.col.requests": "Requests",
        "settings.ai.col.in": "Input (tokens)", "settings.ai.col.out": "Output (tokens)", "settings.ai.col.total": "Total (tokens)",
        "settings.ai.dcol.time": "Time", "settings.ai.dcol.model": "Model", "settings.ai.dcol.provider": "Provider",
        "settings.ai.dcol.in": "In", "settings.ai.dcol.out": "Out", "settings.ai.dcol.total": "Total",
        "settings.ai.dcol.ms": "ms", "settings.ai.dcol.ok": "ok", "settings.ai.refresh_log": "Refresh log",
        # сети
        "settings.net.hint": "Login is allowed only from addresses in allow networks. ⚠️ Do not delete the network you are working from — otherwise you will be logged out at the next check.",
        "settings.net.col.cidr": "CIDR", "settings.net.col.allow": "Allows",
        "settings.net.cidr_label": "CIDR (e.g. 10.0.0.0/8)", "settings.net.allow": "Allows",
        "settings.net.bad_cidr": "Invalid CIDR (e.g. 192.168.0.0/24 or 10.1.2.3/32)", "settings.net.bad_comment": "Invalid comment",
        "settings.net.added": "Network added: {cidr}", "settings.net.pick": "Select a network in the table",
        "settings.net.deleted": "Network deleted: {cidr}", "settings.net.add": "Add network", "settings.net.delete": "Delete selected",
        # API-ключи
        "settings.api.hint": "Keys for `POST /api/script`. Requests run in the context of the specified owner (their roles). The token is shown once on creation — save it.",
        "settings.api.col.owner": "Owner", "settings.api.col.status": "Status", "settings.api.col.created": "Created",
        "settings.api.col.createdby": "Created by", "settings.api.col.expires": "Expires", "settings.api.col.hash": "Hash (prefix)",
        "settings.api.expired": "expired", "settings.api.active": "active", "settings.api.disabled": "disabled", "settings.api.never": "never",
        "settings.api.owner_label": "Owner (username)", "settings.api.ttl": "Lifetime (days, empty = unlimited)",
        "settings.api.need_owner": "Specify the owner (username)", "settings.api.owner_notfound": "User '{owner}' not found",
        "settings.api.created_title": "API key created — save it now",
        "settings.api.created_hint": "The token is shown **once**. Only its hash is stored in the DB.",
        "settings.api.key_label": "API key", "settings.api.enabled_msg": "Key enabled", "settings.api.disabled_msg": "Key disabled",
        "settings.api.deleted": "API key deleted", "settings.api.pick": "Select a key in the table",
        "settings.api.create": "Create key", "settings.api.toggle": "Enable/disable selected", "settings.api.delete_key": "Delete selected",
    },
    "ru": {
        # навигация
        "nav.settings": "Настройки", "nav.secrets": "Секреты", "nav.objects": "Объекты",
        "nav.ai": "AI", "nav.harvester": "Harvester", "nav.history": "История", "nav.logout": "Выход",
        "nav.settings.tip": "Настройки", "nav.secrets.tip": "Хранилище секретов", "nav.objects.tip": "Сохранённые объекты",
        "nav.ai.tip": "LLM чат получения и обработки данных", "nav.harvester.tip": "Исполнение скриптов",
        "nav.history.tip": "История запусков", "nav.logout.tip": "Выход",
        # Settings: язык
        "settings.language.title": "Язык", "settings.language.label": "Язык интерфейса",
        "settings.language.apply": "Применить", "settings.language.saved": "Язык сохранён",
        "settings.language.hint": "Язык применяется после перезагрузки страницы и сохраняется за вашей учётной записью.",
        # заголовки разделов
        "settings.section.appearance": "Внешний вид", "settings.section.account": "Учётная запись",
        "settings.section.users": "Управление пользователями (админ)", "settings.section.ai": "AI-агент: настройки и журнал (админ)",
        "settings.section.networks": "Разрешённые сети (доступ по IP)", "settings.section.apikeys": "API-ключи",
        "settings.error": "Ошибка раздела Settings: {error}",
        # общие
        "settings.common.error": "Ошибка: {error}", "settings.common.yes": "да", "settings.common.no": "нет",
        "settings.common.comment": "Комментарий", "settings.col.comment": "Комментарий",
        "settings.btn.save": "Сохранить", "settings.btn.reset": "Сбросить", "settings.btn.cancel": "Отмена",
        "settings.btn.create": "Создать", "settings.btn.refresh": "Обновить список", "settings.btn.close": "Закрыть",
        # внешний вид
        "settings.appearance.hint": "Персональные настройки оформления. Применяются сразу и сохраняются за вашей учётной записью.",
        "settings.theme.label": "Тема", "settings.theme.dark": "Тёмная", "settings.theme.light": "Светлая",
        "settings.appearance.interface": "Интерфейс", "settings.appearance.tables": "Таблицы и текстовые блоки",
        "settings.appearance.codemirror": "Редактор кода (CodeMirror)", "settings.appearance.colors": "Цвета интерфейса",
        "settings.font.interface": "Шрифт интерфейса", "settings.font.tables": "Шрифт таблиц", "settings.font.size": "Размер, px",
        "settings.codemirror.hint": "Тема редактора задаётся отдельно для тёмной и светлой темы.",
        "settings.codemirror.label": "Тема редактора",
        "settings.colors.hint": "Цвета задаются отдельно для тёмной и светлой темы — редактируется текущая выбранная тема.",
        "settings.color.bg": "Фон", "settings.color.text": "Текст", "settings.color.accent": "Акцент",
        "settings.color.card": "Карточки", "settings.color.title": "Заголовки", "settings.color.panel": "Панели",
        "settings.appearance.save_fail": "Не удалось сохранить: {error}", "settings.appearance.saved": "Внешний вид сохранён",
        # учётная запись
        "settings.account.user": "Пользователь: **{name}**", "settings.account.changepw": "Смена пароля",
        "settings.account.pwrule": "Новый пароль: минимум 17 символов, строчные и прописные буквы, цифра и спецсимвол.",
        "settings.account.pwwarn": "⚠️ После смены пароля вы будете разлогинены на всех своих сессиях — потребуется войти заново.",
        "settings.account.oldpw": "Текущий пароль", "settings.account.newpw": "Новый пароль",
        "settings.account.confirmpw": "Повтор нового пароля", "settings.account.fill": "Заполните текущий и новый пароль",
        "settings.account.mismatch": "Новый пароль и повтор не совпадают",
        "settings.pw.weak": "Новый пароль не соответствует требованиям сложности",
        "settings.account.userfail": "Не удалось получить пользователя: {error}", "settings.account.wrongpw": "Текущий пароль неверный",
        "settings.account.confirm": "Сменить пароль? Вы будете разлогинены на всех своих сессиях.",
        "settings.account.confirm_yes": "Сменить и выйти", "settings.account.changefail": "Не удалось сменить пароль: {error}",
        "settings.account.changed": "Пароль изменён. Выполняется выход…", "settings.account.changebtn": "Сменить пароль",
        "settings.account.meta": "Мои метаданные (JSON)",
        "settings.account.meta_hint": "Произвольные данные вашей учётной записи (например, настройки уведомлений для NOTIFY).",
        "settings.meta.invalid_json": "Метаданные должны быть корректным JSON", "settings.meta.not_object": "Метаданные должны быть JSON-объектом ({...})",
        "settings.meta.save_fail": "Не удалось сохранить метаданные: {error}", "settings.meta.saved": "Метаданные сохранены",
        "settings.meta.savebtn": "Сохранить метаданные",
        # пользователи (админ)
        "settings.users.hint": "Фильтрация по колонкам (username, роли, метаданные). Сброс пароля и блокировка немедленно завершают все сессии пользователя.",
        "settings.users.actions": "Действия по выбранному пользователю", "settings.users.none": "Пользователь не выбран",
        "settings.users.roles": "Роли (JSON-массив)", "settings.users.resetpw": "Новый пароль (сброс)",
        "settings.users.meta": "Метаданные пользователя (JSON)",
        "settings.users.col.username": "Username", "settings.users.col.enabled": "Активна",
        "settings.users.col.roles": "Роли", "settings.users.col.metadata": "Метаданные",
        "settings.users.list_fail": "Ошибка списка пользователей: {error}",
        "settings.users.selected": "Пользователь: {name} ({status})", "settings.users.active": "активна", "settings.users.blocked": "заблокирована",
        "settings.users.block": "Заблокировать", "settings.users.unblock": "Разблокировать", "settings.users.pick": "Выберите пользователя в таблице",
        "settings.users.noselfblock": "Нельзя заблокировать собственную учётную запись",
        "settings.users.unblocked": "Разблокирован: {name}", "settings.users.blocked_done": "Заблокирован: {name} (сессии завершены)",
        "settings.roles.array": "Роли должны быть JSON-массивом", "settings.roles.array_strings": "Роли должны быть JSON-массивом (список строк)",
        "settings.users.roles_saved": "Роли обновлены: {name}", "settings.users.pw_reset": "Пароль сброшен для {name} (сессии завершены)",
        "settings.users.meta_saved": "Метаданные сохранены: {name}",
        "settings.users.save_roles": "Сохранить роли", "settings.users.reset_pw_btn": "Сбросить пароль",
        "settings.users.create": "Создать пользователя", "settings.users.username": "Имя пользователя", "settings.users.password": "Пароль",
        "settings.users.name_rule": "Имя: минимум 3 символа, латиница/цифры/._-", "settings.users.created": "Пользователь создан: {name}",
        # AI
        "settings.ai.limits": "Лимиты", "settings.ai.maxiter": "Макс. действий агента за сессию",
        "settings.ai.limits_saved": "Лимиты AI сохранены", "settings.ai.save_limits": "Сохранить лимиты",
        "settings.ai.log": "Журнал AI — потребление токенов",
        "settings.ai.log_hint": "Сводка по пользователям и детализация запросов (фильтры по колонкам: пользователь, модель, время).",
        "settings.ai.detail": "Детализация запросов", "settings.ai.log_fail": "Ошибка журнала AI: {error}",
        "settings.ai.col.user": "Пользователь", "settings.ai.col.requests": "Запросов",
        "settings.ai.col.in": "Вход (токены)", "settings.ai.col.out": "Выход (токены)", "settings.ai.col.total": "Всего (токены)",
        "settings.ai.dcol.time": "Время", "settings.ai.dcol.model": "Модель", "settings.ai.dcol.provider": "Провайдер",
        "settings.ai.dcol.in": "Вход", "settings.ai.dcol.out": "Выход", "settings.ai.dcol.total": "Всего",
        "settings.ai.dcol.ms": "мс", "settings.ai.dcol.ok": "ok", "settings.ai.refresh_log": "Обновить журнал",
        # сети
        "settings.net.hint": "Вход разрешён только с адресов из разрешающих (allow) сетей. ⚠️ Не удаляйте сеть, из которой работаете сами, — иначе будете разлогинены при следующей проверке.",
        "settings.net.col.cidr": "CIDR", "settings.net.col.allow": "Разрешает",
        "settings.net.cidr_label": "CIDR (напр. 10.0.0.0/8)", "settings.net.allow": "Разрешает",
        "settings.net.bad_cidr": "Некорректный CIDR (пример: 192.168.0.0/24 или 10.1.2.3/32)", "settings.net.bad_comment": "Недопустимый комментарий",
        "settings.net.added": "Сеть добавлена: {cidr}", "settings.net.pick": "Выберите сеть в таблице",
        "settings.net.deleted": "Сеть удалена: {cidr}", "settings.net.add": "Добавить сеть", "settings.net.delete": "Удалить выбранную",
        # API-ключи
        "settings.api.hint": "Ключи для `POST /api/script`. Запросы выполняются в контексте указанного владельца (его роли). Токен показывается один раз при создании — сохраните его.",
        "settings.api.col.owner": "Владелец", "settings.api.col.status": "Статус", "settings.api.col.created": "Создан",
        "settings.api.col.createdby": "Кем создан", "settings.api.col.expires": "Истекает", "settings.api.col.hash": "Хэш (префикс)",
        "settings.api.expired": "истёк", "settings.api.active": "активен", "settings.api.disabled": "выключен", "settings.api.never": "бессрочно",
        "settings.api.owner_label": "Владелец (username)", "settings.api.ttl": "Срок жизни (дней, пусто = бессрочно)",
        "settings.api.need_owner": "Укажите владельца (username)", "settings.api.owner_notfound": "Пользователь '{owner}' не найден",
        "settings.api.created_title": "API-ключ создан — сохраните его сейчас",
        "settings.api.created_hint": "Токен показывается **один раз**. В БД хранится только его хэш.",
        "settings.api.key_label": "API key", "settings.api.enabled_msg": "Ключ включён", "settings.api.disabled_msg": "Ключ выключен",
        "settings.api.deleted": "API-ключ удалён", "settings.api.pick": "Выберите ключ в таблице",
        "settings.api.create": "Создать ключ", "settings.api.toggle": "Вкл/выкл выбранный", "settings.api.delete_key": "Удалить выбранный",
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
