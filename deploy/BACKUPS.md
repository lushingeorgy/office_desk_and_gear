# Автоматические бэкапы БД

Настроены 1 ноября 2026 на сервере `138.16.179.111` для отдельной БД
`office_desk_gear`. Таймер `office-desk-gear-backup.timer` запускается ежедневно
в **04:15 Europe/Moscow**. Следующий запуск после установки: 2 ноября 2026,
04:15 МСК. `Persistent=true` запускает пропущенную задачу после включения сервера.
Работа компьютера пользователя и Codex не требуется. Таймер `maskarad` не изменялся.

Копии находятся в `/opt/projects/office_desk_gear/backups/`. Сохраняются **14 последних
успешных копий**, включая ручные запуски. Каждый каталог содержит `database.dump`
(PostgreSQL custom), `database.toc`, `SHA256SUMS`, `manifest.json`. Каталоги имеют
права 0700, файлы 0600, владелец root.

По запросу пользователя каталог перенесён внутрь проекта из
`/var/backups/office-desk-gear` с проверкой хешей существующих файлов.
`backups/` исключён из Git и контекста Docker; серверный архив также собирается
по списку файлов и не включает этот каталог. При обновлении или переносе проекта
каталог с копиями нужно сохранять вместе с постоянными данными.

`backup-database.sh` проверяет принадлежность контейнера проекту, делает
`pg_dump` средствами той же PostgreSQL 18, проверяет TOC и SHA-256 и публикует
каталог атомарно. Старые копии удаляются только после успешного создания новой;
обрабатываются только каталоги завершённых копий этого проекта. `flock` запрещает
параллельную работу. При обычной ошибке частичная копия удаляется. Скрипт не
останавливает API или PostgreSQL. Низкие приоритеты CPU/IO и ограничение памяти
применены к oneshot-задаче; после завершения постоянный процесс не остаётся.

## Проверка и управление

```sh
systemctl list-timers office-desk-gear-backup.timer --all
systemctl show office-desk-gear-backup.service -p Result -p ExecMainStatus
journalctl -u office-desk-gear-backup.service -n 30 --no-pager
# Ручная копия:
systemctl start office-desk-gear-backup.service
# Проверка выбранной копии из её каталога:
sha256sum --check SHA256SUMS
```

Для повторной установки source-файлы находятся в
`/opt/projects/office_desk_gear/deploy/`:

```sh
install -d -m 0700 /opt/projects/office_desk_gear/backups
chmod 0750 /opt/projects/office_desk_gear/deploy/backup-database.sh
install -m 0644 /opt/projects/office_desk_gear/deploy/office-desk-gear-backup.service /etc/systemd/system/
install -m 0644 /opt/projects/office_desk_gear/deploy/office-desk-gear-backup.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/office-desk-gear-backup.{service,timer}
systemctl daemon-reload
systemctl start office-desk-gear-backup.service
systemctl enable --now office-desk-gear-backup.timer
```

## Проверено на сервере

Первый systemd-запуск завершился с `Result=success`, `ExecMainStatus=0`.
Копия восстановлена в отдельную случайно названную БД `odg_backup_verify_*`,
после проверки эта временная БД удалена. В текущей рабочей БД пока **0 таблиц**:
API ещё ожидает SMTP и начальный запуск. Проверка подтверждает восстановимость
её текущего пустого состояния; будущие таблицы включаются целиком при каждом dump.

На изолированных каталогах проверены ротация 17 старых копий до 14 после новой,
сохранение посторонних файлов и симлинка, сохранение хороших копий и очистка
частичного dump при ошибке, блокировка одновременного запуска. Настоящие копии
не изменялись этими тестами. Санитизированный серверный результат:
`/opt/projects/office_desk_gear/backups/verification.json`; локальные логи —
`tests/artifacts/server_backup_install_latest.log` и `server_backup_verify_latest.log`.

Это копии **БД на том же сервере**. Файлы вложений, `.env.server`, SMTP/JWT-секреты
и конфигурация Caddy в них не входят. Отдельное внешнее хранилище пока не настроено.
Описание формата: [PostgreSQL pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html),
расписания: [systemd.timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html).
