# Очистка истории от секретов (маскировка) — локально и на GitHub

В истории git были захардкожены секреты: зашифрованные объекты `gAAAA…` (Fernet) и
плэйнтекст master key — в `front.py` и `mcp_server.py`, практически в каждом коммите.
Удаление из рабочего дерева НЕ убирает их из истории: любой `git log`/клон их покажет.

> ⚠️ Порядок важен. Шаг 0 (ротация) — обязателен и не заменяется маскировкой.

## 0. Сначала — РОТАЦИЯ (секреты считать скомпрометированными)

Эти значения уже были на GitHub: они могли быть склонированы, форкнуты, закэшированы и
проиндексированы. Маскировка истории НЕ отменяет утечку. Поэтому до/параллельно со scrub:

- сгенерировать новый **master key** и новый **Fernet-ключ**, перешифровать `db_conf` и storage key;
- сменить **пароль/креды БД**, **keycloak client secret**, любые токены/ключи источников, что шифровались;
- инвалидировать выпущенные API-ключи приложения при сомнениях.

Без ротации scrub лишь «приберёт» репозиторий, но утёкшие секреты останутся валидными.

## 1. Рабочее дерево уже чистое

`front.py` — master key через `pwinput`, объекты через аргументы; `mcp_server.py` — секреты из
окружения (`UH2S_DB_CONF` / `UH2S_MASTER_KEY`) или аргументов, дефолты пустые. Проверка:

```
git grep -I -c "gAAAA" -- .      # должно быть пусто
```

Эту очистку рабочего дерева нужно **закоммитить и запушить ДО** scrub (либо запускать
filter-repo на локальном клоне, где коммит уже есть) — иначе свежее зеркало с GitHub его не получит.

## 2. Бэкап (обязательно — операция необратима)

```
git clone --mirror . ../secrets-scrub-backup
```

## 3. Установить git filter-repo

```
pip install git-filter-repo          # либо: brew install git-filter-repo
git filter-repo --version
```

## 4. Подготовить правила замены

```
cp secrets-replacements.txt.example secrets-replacements.txt
# отредактируйте secrets-replacements.txt: regex для gAAAA уже ловит все Fernet-токены;
# ДОБАВЬТЕ literal-строки с реальными значениями master key и прочих плэйнтекст-секретов.
# Файл secrets-replacements.txt в .gitignore — не коммитьте его.
```

## 5. Переписать историю (маскировка во ВСЕХ коммитах и тегах)

filter-repo работает по свежему зеркалу — так безопаснее:

```
git clone --mirror https://github.com/orbitalrailgun/UH2S.git ../UH2S-scrub.git
cd ../UH2S-scrub.git
git filter-repo --replace-text /полный/путь/secrets-replacements.txt
```

Замены применяются к содержимому всех блобов во всей истории и веток/тегов; SHA коммитов изменятся.

## 6. Force-push на GitHub

filter-repo удаляет origin намеренно — добавьте заново и запушьте с перезаписью:

```
git remote add origin https://github.com/orbitalrailgun/UH2S.git
git push --force --all
git push --force --tags
```

## 7. Обновить локальный рабочий клон

Старый клон содержит старые SHA — перезалейте:

```
cd ../UH2S
git fetch origin
git reset --hard origin/main
git for-each-ref --format="%(refname)" refs/tags | xargs -n1 git tag -d   # снести локальные старые теги
git fetch --tags
```

(Или просто склонировать репозиторий заново.)

## 8. Очистка на стороне GitHub

Force-push НЕ удаляет старые коммиты мгновенно — они остаются доступны по SHA из кэша, пока не
произойдёт GC, и могут жить в форках/PR. Поэтому:

- удалить/уведомить про форки; закрыть PR, ссылающиеся на старые коммиты;
- открыть тикет в **GitHub Support** с просьбой очистить кэш недоступных коммитов (stale commits);
- убедиться, что секреты из шага 0 **ротированы** — это единственная настоящая защита.

## Альтернатива без filter-repo — BFG

```
bfg --replace-text secrets-replacements.txt UH2S-scrub.git
cd UH2S-scrub.git && git reflog expire --expire=now --all && git gc --prune=now --aggressive
```

## Памятка

- Все, у кого есть клоны, должны переклонировать (SHA изменились).
- Теги v0.5.0…v0.8.x будут перезаписаны — это нормально.
- `secrets-replacements.txt` и `secrets-scrub-backup/` — в `.gitignore`, не коммитить.
