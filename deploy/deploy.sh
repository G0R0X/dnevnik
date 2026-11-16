#!/usr/bin/env bash
# Обновление проекта на сервере одной командой.
#
# Что делает: проверяет, что на сервере нет ручных правок, тянет изменения из
# git, при необходимости делает бэкап БД и накатывает миграции, ставит новые
# зависимости и перезапускает службы.
#
# Запуск: deploy/deploy.sh (из любой папки, путь к репозиторию берётся от файла).
# Первый раз после клонирования: chmod +x deploy/deploy.sh
#
# Чего не трогает: .env, files/homework/, files/books/*.pdf, logs/ — они в
# .gitignore, поэтому git про них не знает и pull их не удаляет.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

DB_NAME="${DB_NAME:-dnevnik}"
SERVICES=(dnevnik-worker dnevnik-bot)
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"

log() { printf '\n=== %s\n' "$*"; }

# .env в git не лежит и приезжает на сервер отдельно: без него процесс не стартует.
if [ ! -f .env ]; then
    echo "Нет файла .env в $REPO_DIR — обновлять нечего, сначала восстанови секреты." >&2
    exit 1
fi

# Ручные правки на сервере: pull может их перезаписать или отказаться работать.
if [ -n "$(git status --porcelain)" ]; then
    echo "На сервере есть локальные изменения — git pull их не пропустит:" >&2
    git status --short >&2
    echo "" >&2
    echo "Реши: git stash (если правки нужны) или git checkout -- . (если не нужны)." >&2
    exit 1
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD)
BEFORE=$(git rev-parse HEAD)

log "git pull ($BRANCH)"
git pull --ff-only

AFTER=$(git rev-parse HEAD)
if [ "$BEFORE" = "$AFTER" ]; then
    echo "Изменений нет — службы не трогаю."
    exit 0
fi

CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")
echo "Новые коммиты:"
git log --oneline "$BEFORE..$AFTER" | sed 's/^/  /'
echo "Изменённые файлы:"
echo "$CHANGED" | sed 's/^/  /'

# Миграции: сначала бэкап, потом upgrade. Дамп делается через postgres,
# чтобы не разбирать пароль из DATABASE_URL (там префикс +asyncpg).
if grep -q '^alembic/versions/' <<<"$CHANGED"; then
    mkdir -p "$BACKUP_DIR"
    BACKUP="$BACKUP_DIR/dnevnik-$(date +%F-%H%M).sql.gz"
    log "бэкап БД → $BACKUP"
    sudo -u postgres pg_dump "$DB_NAME" | gzip > "$BACKUP"

    log "alembic upgrade head"
    .venv/bin/alembic upgrade head
fi

# Зависимости: только если менялся список пакетов.
if grep -qE '^requirements(-dev)?\.txt$' <<<"$CHANGED"; then
    log "pip install -r requirements.txt"
    .venv/bin/pip install -q -r requirements.txt
fi

# Перезапуск нужен, только если менялся код или зависимости: правка AGENTS.md
# и прочей документации процесса не касается.
if grep -qE '^(src/|requirements)' <<<"$CHANGED"; then
    log "перезапуск служб"
    sudo systemctl restart "${SERVICES[@]}"
    sleep 3

    failed=0
    for service in "${SERVICES[@]}"; do
        if systemctl is-active --quiet "$service"; then
            echo "  $service: active"
        else
            echo "  $service: НЕ РАБОТАЕТ" >&2
            journalctl -u "$service" -n 15 --no-pager >&2
            failed=1
        fi
    done
    [ "$failed" -eq 0 ] || exit 1
fi

log "готово"
