# Сервис автоматического резервного копирования БД (docker-compose, сервис backup).
# База — тот же образ, что и у сервиса postgres, чтобы версия pg_dump совпадала
# с версией сервера: несовпадение мажорных версий — частый источник дампов,
# которые тихо не восстанавливаются.
FROM postgres:16-alpine

# bash    — читаемые скрипты с `set -euo pipefail` (в базовом образе только sh);
# coreutils — GNU `date -d "today HH:MM:SS"` / `date -d "@epoch"`, на которые
#             опирается расчёт времени следующего запуска (busybox date такой
#             синтаксис не понимает);
# python3 — логика ротации (rotate.py), она же покрыта тестом на dev-машине.
RUN apk add --no-cache bash coreutils python3

WORKDIR /app
COPY infra/backup/entrypoint.sh infra/backup/backup.sh infra/backup/rotate.py ./
RUN chmod +x entrypoint.sh backup.sh

# Непривилегированный пользователь (аудит 2026-09-01, CKV_DOCKER_3). Своего не
# заводим — в базовом образе postgres:16-alpine он уже есть (тот же, под которым
# в сервисе postgres лежат данные).
RUN mkdir -p /backups && chown -R postgres:postgres /app /backups
USER postgres

ENTRYPOINT ["./entrypoint.sh"]
