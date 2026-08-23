#!/usr/bin/env bash
# Планировщик резервного копирования (PID 1 контейнера backup).
#
# Без cron-демона: в минимальном alpine-образе вывод cron обычно уходит в
# syslog, а не в stdout контейнера, и «неуспех виден в docker compose logs»
# (требование задачи) превращается в отдельную головную боль. Вместо этого —
# простой бесконечный цикл: спим до следующего целевого времени и запускаем
# backup.sh, весь вывод — в stdout.
#
# Время задаём в UTC, а не по имени зоны Europe/Moscow: у МСК фиксированное
# смещение UTC+3 круглый год (Россия не переходит на летнее время с 2014
# года), поэтому пересчитать «03:00 МСК» в «00:00 UTC» можно один раз здесь,
# в комментарии, и не тащить в образ пакет tzdata.
set -euo pipefail

BACKUP_HOUR_UTC="${BACKUP_HOUR_UTC:-0}"
BACKUP_MINUTE_UTC="${BACKUP_MINUTE_UTC:-0}"

log() {
    printf '%s [backup] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$1"
}

log "Планировщик бэкапов запущен. Ежедневный запуск: $(printf '%02d:%02d' "$BACKUP_HOUR_UTC" "$BACKUP_MINUTE_UTC") UTC (03:00 МСК по умолчанию — самое тихое время суток)."

while true; do
    now_epoch="$(date -u +%s)"
    next_epoch="$(date -u -d "today ${BACKUP_HOUR_UTC}:${BACKUP_MINUTE_UTC}:00" +%s)"
    if [ "$next_epoch" -le "$now_epoch" ]; then
        next_epoch="$(date -u -d "tomorrow ${BACKUP_HOUR_UTC}:${BACKUP_MINUTE_UTC}:00" +%s)"
    fi
    sleep_seconds=$((next_epoch - now_epoch))
    log "Следующий запуск через ${sleep_seconds} сек ($(date -u -d "@${next_epoch}" +'%Y-%m-%d %H:%M:%S') UTC)."
    sleep "$sleep_seconds"

    log "Запуск резервного копирования."
    if /app/backup.sh; then
        log "Резервное копирование завершено успешно."
    else
        status=$?
        log "ОШИБКА: резервное копирование завершилось с кодом ${status}. Следующая попытка — по расписанию завтра."
    fi
done
