# Проверка API Office Desk & Gear

Дата: 27 октября 2026 года.

## Повторная проверка после исправления бронирования

Бронирование исправлено: входные даты с часовым поясом переводятся в локальное
время сервера до проверки пересечений и записи в существующие поля PostgreSQL.
Правило применяется как к одиночной брони, так и к периоду. Существующие даты
без часового пояса сохраняют прежний смысл; миграция данных не требуется.

Повторный прогон: **104 сценария, 102 PASS, 2 FAIL**, охвачены все 32 операции.
Все проверки бронирования прошли: создание с `+03:00`, повтор с ответом 409,
эквивалентные моменты с UTC, `-12:00` и `+14:00`, конфликт с датой без смещения,
пересечение брони пользователя на другом столе и сохранение локальных дат
и времени в периоде. Ошибок HTTP 500 не было. Временная схема удалена.

Два оставшихся FAIL относятся к ранее обнаруженной выдаче административной
роли через регистрацию; это отдельная проблема, не исправлявшаяся в этой задаче.
Актуальные подробности: `tests/artifacts/api_results.json`.

Ниже сохранены результаты первоначальной проверки до исправления.

## Результат

Проверено 32 из 32 API-операций (метод + маршрут). Из 97 сценариев прошли 94, не прошли 3. Три неуспешных сценария подтверждают две проблемы. Это функциональная проверка выбранных сценариев, а не гарантия отсутствия других ошибок.

## Процесс проверки

1. Прочитаны README, маршруты FastAPI, схемы запросов, сервисы и репозитории.
2. Проверены импорты зависимостей, подключение к PostgreSQL 18.6 и согласованность пакетов (`pip check`: No broken requirements found). Указанные в README тесты test_postgresql.py и test_launcher.py отсутствовали.
3. Создана отдельная схема api_check_<случайный UUID> в настроенной PostgreSQL. Сервер запущен отдельным процессом на свободном локальном порту. PGOPTIONS задавал search_path только этой схемы; тестовые данные создавались через штатное начальное заполнение.
4. Выполнены настоящие HTTP-запросы через requests к запущенному Uvicorn. Проверялись HTTP-коды и содержимое ответов в ключевых сценариях; подмена БД и сервисов не использовалась.
5. Проверены три роли, все 22 закрытые операции без токена, регистрация, вход и выход, профиль, фильтры столов, оборудование, PNG QR-код, заявки, бронирования, отмены, конфликт времени, аналитика и сброс пароля.
6. После завершения остановлен только тестовый сервер, удалена только созданная схема. Рабочие таблицы не изменялись. Код сброса пароля скрыт в сохранённом журнале.

## Найденные проблемы

### 1. Высокий приоритет: самостоятельная выдача прав администратора

POST /api/v1/auth/register принимает публичный параметр role=it_admin и возвращает 200. После входа этот аккаунт успешно вызывает GET /api/v1/admin/users (200) и получает список пользователей. Ожидался запрет выдачи административных прав при публичной регистрации и отсутствие доступа к списку.

Причина: server/services/user_service.py:75–83 принимает любую роль из ROLES и сохраняет её. Существующая проверка ролей на административном маршруте работает, но саму роль можно получить без административного доступа.

Рекомендация: публично создавать только employee; назначение административных ролей вынести в отдельную защищённую процедуру. Если выбор привилегированной роли предусмотрен учебным заданием, это поведение нужно явно считать ограничением безопасности.

### 2. Ошибка бронирования: HTTP 500 при повторном запросе с часовым поясом

POST /api/v1/bookings с planed_at в формате YYYY-MM-DDT09:00:00+03:00 в первом запросе возвращает 200. Повтор того же запроса возвращает 500 вместо контролируемого отказа из-за пересечения времени.

Подтверждение журнала: TypeError: can't compare offset-naive and offset-aware datetimes. Источник: server/repositories/booking_repository.py:79, сравнение booking.ends_at > start. В booking_service.py:165 часовой пояс входной даты сохраняется, а из БД возвращается дата без часового пояса.

Рекомендация: согласовать хранение и обработку времени, например приводить к UTC и использовать timezone-aware значения во всей цепочке, либо явно запрещать такой формат валидацией. Добавить регрессионную проверку пересечений.

## Охват маршрутов

| Группа | Операций |
|---|---:|
| system | 2 |
| auth | 5 |
| users | 2 |
| desks | 2 |
| bookings | 6 |
| equipment | 2 |
| tickets | 2 |
| admin: desks | 2 |
| admin: bookings | 1 |
| admin: equipment | 4 |
| admin: tickets | 2 |
| admin: reports | 1 |
| admin: users | 1 |

Все операции имеют успешный функциональный вызов. Обнаруженные ошибки относятся к дополнительным сценариям безопасности и повторному бронированию с часовым поясом.

## Повторный запуск

Из корня проекта в PowerShell:

```powershell
.\.venv\Scripts\python.exe tests/check_api.py
```

Нужны настроенный .env, работающий PostgreSQL, права на создание/удаление схемы и доступ к локальным TCP-портам. Скрипт сам создаёт временную схему и удаляет её в finally. Код завершения 1 при текущих дефектах ожидаем: проверки действительно обнаружили проблемы.

Скрипт: tests/check_api.py. Машиночитаемые результаты: tests/artifacts/api_results.json. Журнал: tests/artifacts/server.log. Каталог artifacts исключён из Git существующим .gitignore.

## Ограничения

Проверка выполнена локально с реальным PostgreSQL и тестовыми данными. Нагрузочное тестирование, параллельные запросы, работа с внешней сетью, мобильный интерфейс и доставка писем не проверялись. Сброс пароля проверен через код в консоли, как реализовано приложением. Все варианты входных данных не перебирались. Код приложения не исправлялся.

## Все сценарии

| № | Сценарий | Метод и путь | Ожидался HTTP | Получен HTTP | Итог |
|---:|---|---|---|---:|---|
| 1 | Health | `GET /health` | 200 | 200 | PASS |
| 2 | Service identity | `GET /api/v1/system/info` | 200 | 200 | PASS |
| 3 | Swagger UI | `GET /docs` | 200 | 200 | PASS |
| 4 | OpenAPI | `GET /openapi.json` | 200 | 200 | PASS |
| 5 | Auth required DELETE /admin/bookings/{booking_id} | `DELETE /api/v1/admin/bookings/999999` | 401 | 401 | PASS |
| 6 | Auth required DELETE /bookings/{booking_id} | `DELETE /api/v1/bookings/999999` | 401 | 401 | PASS |
| 7 | Auth required GET /admin/analytics | `GET /api/v1/admin/analytics` | 401 | 401 | PASS |
| 8 | Auth required GET /admin/equipment | `GET /api/v1/admin/equipment` | 401 | 401 | PASS |
| 9 | Auth required GET /admin/equipment/{equipment_id}/qr | `GET /api/v1/admin/equipment/999999/qr` | 401 | 401 | PASS |
| 10 | Auth required GET /admin/tickets | `GET /api/v1/admin/tickets` | 401 | 401 | PASS |
| 11 | Auth required GET /admin/users | `GET /api/v1/admin/users` | 401 | 401 | PASS |
| 12 | Auth required GET /bookings/active | `GET /api/v1/bookings/active` | 401 | 401 | PASS |
| 13 | Auth required GET /bookings/history | `GET /api/v1/bookings/history` | 401 | 401 | PASS |
| 14 | Auth required GET /equipment/my | `GET /api/v1/equipment/my` | 401 | 401 | PASS |
| 15 | Auth required GET /tickets/my | `GET /api/v1/tickets/my` | 401 | 401 | PASS |
| 16 | Auth required GET /users/me | `GET /api/v1/users/me` | 401 | 401 | PASS |
| 17 | Auth required PATCH /admin/desks/{desk_id} | `PATCH /api/v1/admin/desks/999999` | 401 | 401 | PASS |
| 18 | Auth required PATCH /admin/equipment/{equipment_id}/assign | `PATCH /api/v1/admin/equipment/999999/assign` | 401 | 401 | PASS |
| 19 | Auth required PATCH /admin/tickets/{ticket_id}/status | `PATCH /api/v1/admin/tickets/999999/status` | 401 | 401 | PASS |
| 20 | Auth required PATCH /users/me | `PATCH /api/v1/users/me` | 401 | 401 | PASS |
| 21 | Auth required POST /admin/desks | `POST /api/v1/admin/desks` | 401 | 401 | PASS |
| 22 | Auth required POST /admin/equipment | `POST /api/v1/admin/equipment` | 401 | 401 | PASS |
| 23 | Auth required POST /auth/logout | `POST /api/v1/auth/logout` | 401 | 401 | PASS |
| 24 | Auth required POST /bookings | `POST /api/v1/bookings` | 401 | 401 | PASS |
| 25 | Auth required POST /bookings/period | `POST /api/v1/bookings/period` | 401 | 401 | PASS |
| 26 | Auth required POST /tickets | `POST /api/v1/tickets` | 401 | 401 | PASS |
| 27 | Login employee | `POST /api/v1/auth/login` | 200 | 200 | PASS |
| 28 | Login it_admin | `POST /api/v1/auth/login` | 200 | 200 | PASS |
| 29 | Login office_manager | `POST /api/v1/auth/login` | 200 | 200 | PASS |
| 30 | Register employee | `POST /api/v1/auth/register` | 200 | 200 | PASS |
| 31 | Duplicate email | `POST /api/v1/auth/register` | 400 | 400 | PASS |
| 32 | Invalid email | `POST /api/v1/auth/register` | 400 | 400 | PASS |
| 33 | Short password | `POST /api/v1/auth/register` | 422 | 422 | PASS |
| 34 | Wrong password | `POST /api/v1/auth/login` | 401 | 401 | PASS |
| 35 | Missing token | `GET /api/v1/users/me` | 401 | 401 | PASS |
| 36 | Invalid token | `GET /api/v1/users/me` | 401 | 401 | PASS |
| 37 | Own profile | `GET /api/v1/users/me` | 200 | 200 | PASS |
| 38 | Update profile | `PATCH /api/v1/users/me` | 200 | 200 | PASS |
| 39 | Employee admin access denied | `GET /api/v1/admin/users` | 403 | 403 | PASS |
| 40 | Manager equipment access denied | `GET /api/v1/admin/equipment` | 403 | 403 | PASS |
| 41 | Admin users | `GET /api/v1/admin/users` | 200 | 200 | PASS |
| 42 | Desk features | `GET /api/v1/desk-features` | 200 | 200 | PASS |
| 43 | Desks list | `GET /api/v1/desks` | 200 | 200 | PASS |
| 44 | Desk feature filtering | `GET /api/v1/desks?features=1,2` | 200 | 200 | PASS |
| 45 | Invalid feature filter | `GET /api/v1/desks?features=abc` | 422 | 422 | PASS |
| 46 | Soft filter | `GET /api/v1/desks?sockets=999` | 200 | 200 | PASS |
| 47 | Create desk | `POST /api/v1/admin/desks` | 200 | 200 | PASS |
| 48 | Duplicate desk | `POST /api/v1/admin/desks` | 409 | 409 | PASS |
| 49 | Update desk | `PATCH /api/v1/admin/desks/5` | 200 | 200 | PASS |
| 50 | Missing desk update | `PATCH /api/v1/admin/desks/999999` | 404 | 404 | PASS |
| 51 | Create equipment | `POST /api/v1/admin/equipment` | 200 | 200 | PASS |
| 52 | Duplicate serial | `POST /api/v1/admin/equipment` | 409 | 409 | PASS |
| 53 | Equipment detail | `GET /api/v1/equipment/3` | 200 | 200 | PASS |
| 54 | Missing equipment | `GET /api/v1/equipment/999999` | 404 | 404 | PASS |
| 55 | Equipment list | `GET /api/v1/admin/equipment` | 200 | 200 | PASS |
| 56 | Assign equipment | `PATCH /api/v1/admin/equipment/3/assign` | 200 | 200 | PASS |
| 57 | My equipment | `GET /api/v1/equipment/my` | 200 | 200 | PASS |
| 58 | QR PNG generation | `GET /api/v1/admin/equipment/3/qr` | 200 | 200 | PASS |
| 59 | Create ticket | `POST /api/v1/tickets` | 200 | 200 | PASS |
| 60 | My tickets | `GET /api/v1/tickets/my` | 200 | 200 | PASS |
| 61 | All tickets | `GET /api/v1/admin/tickets` | 200 | 200 | PASS |
| 62 | Ticket status | `PATCH /api/v1/admin/tickets/1/status` | 200 | 200 | PASS |
| 63 | Invalid ticket status | `PATCH /api/v1/admin/tickets/1/status` | 422 | 422 | PASS |
| 64 | Empty issue | `POST /api/v1/tickets` | 422 | 422 | PASS |
| 65 | Create booking | `POST /api/v1/bookings` | 200 | 200 | PASS |
| 66 | Overlapping booking | `POST /api/v1/bookings` | 409 | 409 | PASS |
| 67 | Booking list/date filter | `GET /api/v1/bookings?desk_id=5&date=2026-10-29` | 200 | 200 | PASS |
| 68 | Booking history | `GET /api/v1/bookings/history` | 200 | 200 | PASS |
| 69 | No current booking | `GET /api/v1/bookings/active` | 200 | 200 | PASS |
| 70 | Foreign booking cancellation denied | `DELETE /api/v1/bookings/1` | 403 | 403 | PASS |
| 71 | Cancel own booking | `DELETE /api/v1/bookings/1` | 204 | 204 | PASS |
| 72 | Repeated cancellation | `DELETE /api/v1/bookings/1` | 404 | 404 | PASS |
| 73 | Period booking | `POST /api/v1/bookings/period` | 200 | 200 | PASS |
| 74 | Period conflict skips | `POST /api/v1/bookings/period` | 200 | 200 | PASS |
| 75 | Admin cancellation requires reason | `DELETE /api/v1/admin/bookings/2` | 422 | 422 | PASS |
| 76 | Admin cancellation | `DELETE /api/v1/admin/bookings/2` | 204 | 204 | PASS |
| 77 | Analytics default | `GET /api/v1/admin/analytics` | 200 | 200 | PASS |
| 78 | Analytics with data | `GET /api/v1/admin/analytics?start_date=2026-10-29&end_date=2026-10-31` | 200 | 200 | PASS |
| 79 | Invalid analytics period | `GET /api/v1/admin/analytics?start_date=2026-10-28&end_date=2026-10-27` | 422 | 422 | PASS |
| 80 | SECURITY public admin registration denied | `POST /api/v1/auth/register` | 400, 403, 422 | 200 | FAIL |
| 81 | Privilege test login | `POST /api/v1/auth/login` | 200 | 200 | PASS |
| 82 | SECURITY self-registered admin cannot list users | `GET /api/v1/admin/users` | 403 | 200 | FAIL |
| 83 | Timezone-aware booking handled without server error | `POST /api/v1/bookings` | 200, 400, 409, 422 | 200 | PASS |
| 84 | Repeated timezone-aware booking rejects overlap | `POST /api/v1/bookings` | 400, 409, 422 | 500 | FAIL |
| 85 | Create current booking | `POST /api/v1/bookings` | 200 | 200 | PASS |
| 86 | Current booking returned | `GET /api/v1/bookings/active` | 200 | 200 | PASS |
| 87 | Desk marked occupied | `GET /api/v1/desks?floor=9` | 200 | 200 | PASS |
| 88 | Cancel current booking | `DELETE /api/v1/bookings/6` | 204 | 204 | PASS |
| 89 | Reset test login | `POST /api/v1/auth/login` | 200 | 200 | PASS |
| 90 | Password reset request | `POST /api/v1/auth/password-reset/request` | 200 | 200 | PASS |
| 91 | Wrong reset code | `POST /api/v1/auth/password-reset/confirm` | 400 | 400 | PASS |
| 92 | Password reset confirm | `POST /api/v1/auth/password-reset/confirm` | 200 | 200 | PASS |
| 93 | Reset revokes sessions | `GET /api/v1/users/me` | 401 | 401 | PASS |
| 94 | Old password rejected | `POST /api/v1/auth/login` | 401 | 401 | PASS |
| 95 | New password accepted | `POST /api/v1/auth/login` | 200 | 200 | PASS |
| 96 | Logout | `POST /api/v1/auth/logout` | 204 | 204 | PASS |
| 97 | Logout revokes token | `GET /api/v1/users/me` | 401 | 401 | PASS |
