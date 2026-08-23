#!/usr/bin/env bash
# Один прогон резервного копирования: pg_dump -> gzip -> ротация.
# Вызывается из entrypoint.sh по расписанию (docker-compose, сервис backup).
#
# Реквизиты БД — те же переменные окружения, что у сервиса postgres, но в виде
# стандартных для libpq имён (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE), их
# pg_dump читает сам. Пароль нигде не печатается и не передаётся аргументом
# командной строки (аргументы процесса видны через `ps` всем в контейнере).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
    printf '%s [backup] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$1"
}

mkdir -p "$BACKUP_DIR"

timestamp="$(date -u +'%Y-%m-%dT%H%M%SZ')"
dump_file="${BACKUP_DIR}/${PGDATABASE:-db}-${timestamp}.sql.gz"
tmp_file="${dump_file}.part"

log "Старт pg_dump: база '${PGDATABASE:-?}' на '${PGHOST:-?}' -> ${dump_file}"

# Дампим во временный файл и переименовываем только при успехе: если pg_dump
# или gzip упадут на середине, в каталоге не останется недописанного *.sql.gz,
# который ротация могла бы принять за валидный свежий бэкап.
if pg_dump --no-password | gzip > "$tmp_file"; then
    mv "$tmp_file" "$dump_file"
    size="$(du -h "$dump_file" | cut -f1)"
    log "УСПЕХ: дамп создан, ${dump_file} (${size})."
else
    status=$?
    rm -f "$tmp_file"
    log "ОШИБКА: pg_dump/gzip завершились с кодом ${status}, дамп НЕ создан."
    exit "$status"
fi

log "Ротация: храним последние ${BACKUP_KEEP_DAYS} копий в ${BACKUP_DIR}."
if python3 "${SCRIPT_DIR}/rotate.py" "$BACKUP_DIR" "$BACKUP_KEEP_DAYS"; then
    log "Ротация завершена без ошибок."
else
    status=$?
    log "ОШИБКА: ротация завершилась с кодом ${status} (дамп при этом создан и не потерян)."
    exit "$status"
fi
