# План реализации надёжного ядра системы бронирования

> **Для агентной реализации:** ОБЯЗАТЕЛЬНЫЙ ДОПОЛНИТЕЛЬНЫЙ НАВЫК: использовать `superpowers:subagent-driven-development` (рекомендуется) или `superpowers:executing-plans` для выполнения плана по задачам. Шаги отслеживаются флажками `- [ ]`.

**Цель:** Создать надёжное финансовое и операционное ядро бота с историей платежей, автоматическими статусами проживания, редактированием заявок, уведомлениями, мониторингом ошибок и полным набором тестов.

**Архитектура:** Новая бизнес-логика размещается в небольших сервисах и работает только через короткие транзакции `get_session()`. Старый `legacy.py` постепенно переводится на сервисы без одновременного переписывания всего бота. После каждого блока выполняются тесты, отдельный коммит, публикация на Bothost и проверка логов.

**Технологии:** Python 3.11+, aiogram 3, SQLAlchemy, SQLite, unittest, asyncio, zoneinfo, Bothost, GitHub.

---

## Структура файлов

Новые файлы:

- `services/payment_service.py` — история платежей и финансовый баланс.
- `services/booking_management_service.py` — перенос дат, смена проживания и перерасчёт.
- `services/stay_service.py` — статусы проживания и автоматические переходы.
- `services/booking_card_service.py` — единое представление карточки заявки.
- `services/daily_summary_service.py` — ежедневная административная сводка.
- `services/error_monitor_service.py` — группировка критических ошибок.
- `bot/routers/admin_payments.py` — кнопки добавления оплаты, возврата и корректировки.
- `bot/routers/admin_booking_edit.py` — перенос дат, смена проживания и статусы проживания.
- `tests/helpers.py` — временная БД, тестовый бот и фикстуры.
- `tests/test_payment_service.py`
- `tests/test_booking_management_service.py`
- `tests/test_stay_service.py`
- `tests/test_booking_card_service.py`
- `tests/test_daily_summary_service.py`
- `tests/test_error_monitor_service.py`
- `tests/test_notification_workflows.py`
- `tests/test_booking_e2e.py`

Изменяемые файлы:

- `db/models.py`
- `db/migrations.py`
- `db/session.py`
- `database.py`
- `config.py`
- `bot/states.py`
- `bot/keyboards.py`
- `bot/routers/legacy.py`
- `bot/routers/my_bookings.py`
- `bot/routers/admin_tools.py`
- `handlers.py`
- `main.py`
- `services/financial_service.py`
- `services/notification_service.py`
- `utils.py`

## Задача 1: Резервная копия и исходная проверка

**Файлы:**
- Использовать: `services/backup_service.py`
- Создать: `backups/project_full/booking_bot2_before_reliable_core_<timestamp>.zip`
- Создать: `backups/backup_<timestamp>.db`

- [ ] **Шаг 1: Создать резервную копию базы**

Запустить:

```powershell
python -c "from services.backup_service import create_database_backup; print(create_database_backup(timestamped=True))"
```

Ожидается: путь к новой копии `.db`.

- [ ] **Шаг 2: Создать полный ZIP проекта**

Архивировать исходники, фотографии, конфигурацию и текущую БД. Исключить `.git`, `__pycache__`,
старые архивы, `deploy` и временные отчёты.

- [ ] **Шаг 3: Проверить резервные копии**

Проверить открытие ZIP и выполнить:

```powershell
python -c "import sqlite3; c=sqlite3.connect(r'<путь-к-копии-db>'); print(c.execute('PRAGMA integrity_check').fetchone()[0])"
```

Ожидается: `ok`.

- [ ] **Шаг 4: Запустить исходные проверки**

```powershell
python -m unittest discover -s tests -v
python -c "import main; import handlers; import bot.routers.legacy; print('imports ok')"
```

- [ ] **Шаг 5: Зафиксировать резервную точку**

```powershell
git tag reliable-core-start-2026-06-12
git push origin reliable-core-start-2026-06-12
```

## Задача 2: Новые модели и миграции

**Файлы:**
- Изменить: `db/models.py`
- Изменить: `db/migrations.py`
- Изменить: `database.py`
- Создать тест: `tests/test_reliable_core_migrations.py`

- [ ] **Шаг 1: Написать падающий тест миграций**

Тест создаёт старую таблицу `bookings`, запускает миграции и проверяет наличие:

```python
expected_booking_columns = {
    "calculated_total",
    "payment_status",
    "stay_status",
    "stay_status_changed_at",
}
expected_tables = {"payment_transactions", "booking_changes", "error_events"}
```

- [ ] **Шаг 2: Проверить ожидаемое падение**

```powershell
python -m unittest tests.test_reliable_core_migrations -v
```

Ожидается: FAIL из-за отсутствующих таблиц и колонок.

- [ ] **Шаг 3: Добавить модели**

Добавить `PaymentTransaction`, `BookingChange`, `ErrorEvent` и новые колонки `Booking`.
Для времени использовать `datetime.now`, для финансовых значений — целые рубли.

- [ ] **Шаг 4: Добавить безопасную миграцию**

`run_migrations()` создаёт таблицы и добавляет отсутствующие колонки без изменения существующих
заявок. Для старых заявок первоначальные значения:

```python
payment_status = None
stay_status = "awaiting_checkin"
calculated_total = manual_total
```

- [ ] **Шаг 5: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_reliable_core_migrations -v
git add db/models.py db/migrations.py database.py tests/test_reliable_core_migrations.py
git commit -m "Добавить модели платежей, изменений и статусов"
```

## Задача 3: Сервис платежей и история операций

**Файлы:**
- Создать: `services/payment_service.py`
- Изменить: `services/financial_service.py`
- Создать тест: `tests/test_payment_service.py`

- [ ] **Шаг 1: Написать падающие тесты**

Проверить:

```python
add_payment(booking_id, 6000, "перевод", admin_id)
add_payment(booking_id, 4000, "наличные", admin_id)
add_refund(booking_id, 2000, "возврат", admin_id)
```

Ожидаемый итог: внесено `10000`, возвращено `2000`, чистая оплата `8000`.

Отдельно проверить статусы `partially_paid`, `paid`, `overpaid`, `refunded` и запрет отрицательной
суммы.

- [ ] **Шаг 2: Проверить ожидаемое падение**

```powershell
python -m unittest tests.test_payment_service -v
```

- [ ] **Шаг 3: Реализовать сервис**

Публичные функции:

```python
def get_payment_balance(db_session, booking_id: int) -> dict[str, int]: ...
def preview_payment(db_session, booking_id: int, amount: int) -> dict[str, int]: ...
def add_payment(booking_id: int, amount: int, method: str, admin_id: int, comment: str = "") -> dict[str, int]: ...
def add_refund(booking_id: int, amount: int, method: str, admin_id: int, comment: str) -> dict[str, int]: ...
def add_adjustment(booking_id: int, amount: int, admin_id: int, comment: str) -> dict[str, int]: ...
```

Каждая операция обновляет совместимое поле `paid_amount`, определяет `payment_status` и проверяет
сохранение через новую сессию.

- [ ] **Шаг 4: Перенести подтверждение чека и админскую корректировку на сервис**

Изменить `bot/routers/legacy.py` и `bot/routers/admin_tools.py`, удалив прямое присваивание
`paid_amount`.

- [ ] **Шаг 5: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_payment_service tests.test_financial_service -v
git add services/payment_service.py services/financial_service.py bot/routers/legacy.py bot/routers/admin_tools.py tests/test_payment_service.py
git commit -m "Добавить историю платежей и надёжные операции оплаты"
```

## Задача 4: Административная кнопка «Добавить оплату»

**Файлы:**
- Создать: `bot/routers/admin_payments.py`
- Изменить: `bot/states.py`
- Изменить: `bot/keyboards.py`
- Изменить: `handlers.py`
- Создать тест: `tests/test_admin_payment_workflow.py`

- [ ] **Шаг 1: Написать падающий тест сценария**

Проверить цепочку:

```text
Карточка заявки → ➕ Добавить оплату → 5000 → Перевод → подтверждение
```

Тест проверяет новую транзакцию, увеличение внесённой суммы и уведомление гостю.

- [ ] **Шаг 2: Добавить состояния и роутер**

Состояния:

```python
waiting_for_payment_amount
waiting_for_payment_method
waiting_for_overpayment_confirmation
waiting_for_payment_adjustment_comment
```

- [ ] **Шаг 3: Реализовать предупреждение о переплате**

Если после доплаты `overpaid > 0`, бот показывает сумму переплаты и требует кнопку
`⚠️ Подтвердить переплату`.

- [ ] **Шаг 4: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_admin_payment_workflow -v
git add bot/routers/admin_payments.py bot/states.py bot/keyboards.py handlers.py tests/test_admin_payment_workflow.py
git commit -m "Добавить администратору пошаговое внесение оплаты"
```

## Задача 5: Единая карточка заявки

**Файлы:**
- Создать: `services/booking_card_service.py`
- Изменить: `bot/routers/my_bookings.py`
- Изменить: `bot/routers/legacy.py`
- Создать тест: `tests/test_booking_card_service.py`

- [ ] **Шаг 1: Написать падающие тесты карточек**

Проверить карточки для частичной оплаты, полной оплаты, переплаты и возврата. Карточка гостя не
содержит административные заметки, карточка администратора содержит их.

- [ ] **Шаг 2: Реализовать структуру и форматтеры**

```python
def build_booking_card_data(booking_id: int) -> dict: ...
def format_guest_booking_card(data: dict) -> str: ...
def format_admin_booking_card(data: dict) -> str: ...
```

- [ ] **Шаг 3: Перевести пользовательские и админские карточки на сервис**

Удалить локальные расчёты сумм и подписей статусов из затронутых роутеров.

- [ ] **Шаг 4: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_booking_card_service -v
git add services/booking_card_service.py bot/routers/my_bookings.py bot/routers/legacy.py tests/test_booking_card_service.py
git commit -m "Унифицировать карточки заявок"
```

## Задача 6: Перенос дат и смена проживания

**Файлы:**
- Создать: `services/booking_management_service.py`
- Создать: `bot/routers/admin_booking_edit.py`
- Изменить: `bot/states.py`
- Изменить: `bot/keyboards.py`
- Изменить: `handlers.py`
- Создать тест: `tests/test_booking_management_service.py`
- Создать тест: `tests/test_admin_booking_edit_workflow.py`

- [ ] **Шаг 1: Написать падающие тесты сервиса**

Проверить:

- занятые даты отклоняются;
- свободные даты сохраняются;
- новый номер проверяется по доступности;
- стоимость пересчитывается;
- история платежей сохраняется;
- создаётся `BookingChange`;
- баланс показывает остаток или переплату.

- [ ] **Шаг 2: Реализовать сервис**

```python
def preview_date_change(booking_id: int, date_from: date, date_to: date) -> dict: ...
def apply_date_change(booking_id: int, date_from: date, date_to: date, actor_id: int) -> dict: ...
def preview_room_change(booking_id: int, room_type: str) -> dict: ...
def apply_room_change(booking_id: int, room_type: str, actor_id: int) -> dict: ...
```

- [ ] **Шаг 3: Реализовать календарный сценарий переноса**

Использовать существующий генератор календаря. Месяцы переключаются inline-кнопками, действия
выбираются нижней клавиатурой.

- [ ] **Шаг 4: Реализовать сценарий смены проживания**

Показывать допустимые комбинации, отмечать занятые и показывать предварительный перерасчёт.

- [ ] **Шаг 5: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_booking_management_service tests.test_admin_booking_edit_workflow -v
git add services/booking_management_service.py bot/routers/admin_booking_edit.py bot/states.py bot/keyboards.py handlers.py tests/test_booking_management_service.py tests/test_admin_booking_edit_workflow.py
git commit -m "Добавить перенос дат и смену проживания"
```

## Задача 7: Статусы проживания

**Файлы:**
- Создать: `services/stay_service.py`
- Изменить: `bot/routers/admin_booking_edit.py`
- Изменить: `main.py`
- Создать тест: `tests/test_stay_service.py`

- [ ] **Шаг 1: Написать падающие тесты переходов**

Для `Asia/Irkutsk` проверить:

```text
до 14:00 даты заезда → awaiting_checkin
после 14:00 даты заезда → checked_in
после 12:00 даты выезда → checked_out
после запроса отзыва → completed
```

- [ ] **Шаг 2: Реализовать сервис**

```python
def calculate_due_stay_status(booking, now: datetime) -> str: ...
def apply_due_stay_transitions(now: datetime | None = None) -> list[int]: ...
def set_stay_status(booking_id: int, status: str, actor_id: int, comment: str = "") -> None: ...
```

- [ ] **Шаг 3: Зарегистрировать фоновую задачу**

Проверять переходы раз в минуту. Повторный запуск не создаёт повторных записей истории.

- [ ] **Шаг 4: Добавить ручную коррекцию в админскую карточку**

Кнопка `🏠 Статус проживания` показывает допустимые статусы и записывает изменение в историю.

- [ ] **Шаг 5: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_stay_service -v
git add services/stay_service.py bot/routers/admin_booking_edit.py main.py tests/test_stay_service.py
git commit -m "Добавить автоматические статусы проживания"
```

## Задача 8: Напоминания с остатком и завершение проживания

**Файлы:**
- Изменить: `services/notification_service.py`
- Изменить: `utils.py`
- Изменить: `main.py`
- Создать тест: `tests/test_notification_workflows.py`

- [ ] **Шаг 1: Написать падающие тесты уведомлений**

Проверить уведомления за 7, 3 и 1 день, в день заезда, в день выезда и после проживания.
Все сообщения с остатком содержат строку `Осталось оплатить`.

- [ ] **Шаг 2: Реализовать идемпотентную отправку**

Перед отправкой проверять `NotificationLog`, после успешной отправки создавать запись.

- [ ] **Шаг 3: Связать запрос отзыва со статусом `completed`**

После успешной отправки просьбы об отзыве вызвать сервис проживания.

- [ ] **Шаг 4: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_notification_workflows -v
git add services/notification_service.py utils.py main.py tests/test_notification_workflows.py
git commit -m "Улучшить напоминания и завершение проживания"
```

## Задача 9: Ежедневная сводка в 09:00

**Файлы:**
- Создать: `services/daily_summary_service.py`
- Изменить: `main.py`
- Создать тест: `tests/test_daily_summary_service.py`

- [ ] **Шаг 1: Написать падающий тест сводки**

Тестовая база содержит заезд, выезд, остаток оплаты, просроченную заявку и заселённого гостя.
Проверить присутствие каждого блока и отсутствие пустых секций.

- [ ] **Шаг 2: Реализовать формирование и отправку**

```python
def build_daily_summary(target_date: date) -> str: ...
async def schedule_daily_summary(bot) -> None: ...
```

Планировщик работает по `Asia/Irkutsk` и использует `NotificationLog` для защиты от дублей.

- [ ] **Шаг 3: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_daily_summary_service -v
git add services/daily_summary_service.py main.py tests/test_daily_summary_service.py
git commit -m "Добавить ежедневную административную сводку"
```

## Задача 10: Группировка критических ошибок

**Файлы:**
- Создать: `services/error_monitor_service.py`
- Изменить: `main.py`
- Создать тест: `tests/test_error_monitor_service.py`

- [ ] **Шаг 1: Написать падающие тесты**

Проверить: первая ошибка отправляется сразу; следующие одинаковые ошибки не отправляются сразу;
через 30 минут формируется сводка с количеством повторов.

- [ ] **Шаг 2: Реализовать сервис**

```python
def make_error_fingerprint(exc: BaseException, traceback_text: str) -> str: ...
def record_error(exc: BaseException, traceback_text: str) -> dict: ...
async def flush_error_summaries(bot) -> None: ...
```

- [ ] **Шаг 3: Подключить к существующему Telegram-обработчику ошибок**

Первая ошибка отправляется сразу, повторения только учитываются. Фоновая задача проверяет сводки
раз в минуту.

- [ ] **Шаг 4: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_error_monitor_service -v
git add services/error_monitor_service.py main.py tests/test_error_monitor_service.py
git commit -m "Сгруппировать повторяющиеся критические ошибки"
```

## Задача 11: Полные сквозные тесты

**Файлы:**
- Создать: `tests/helpers.py`
- Создать: `tests/test_booking_e2e.py`
- Изменить: существующие тесты при необходимости

- [ ] **Шаг 1: Создать изолированную тестовую среду**

Фикстура создаёт временную SQLite-базу, тестового администратора, гостя и поддельный Telegram
транспорт, сохраняющий отправленные сообщения.

- [ ] **Шаг 2: Реализовать сквозной финансовый сценарий**

```text
Бронирование → подтверждение → скриншот → частичная оплата → доплата → полная оплата
```

Проверить БД через новую сессию после каждого шага.

- [ ] **Шаг 3: Реализовать сквозной операционный сценарий**

```text
Перенос дат → смена проживания → напоминание → заселение → выселение → отзыв → завершение
```

- [ ] **Шаг 4: Реализовать тест перезапуска**

Дважды запустить планировщики и проверить отсутствие повторных уведомлений и изменений истории.

- [ ] **Шаг 5: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_booking_e2e -v
git add tests/helpers.py tests/test_booking_e2e.py
git commit -m "Добавить сквозные тесты жизненного цикла заявки"
```

## Задача 12: Удаление глобальной session из всех затронутых сценариев

**Файлы:**
- Изменить: `bot/routers/legacy.py`
- Изменить: `bot/routers/my_bookings.py`
- Изменить: `bot/routers/admin_tools.py`
- Изменить: новые роутеры и сервисы
- Создать тест: `tests/test_no_global_session_in_core.py`

- [ ] **Шаг 1: Написать архитектурный тест**

Тест проверяет отсутствие импорта глобальной `session` в новых сервисах и новых роутерах, а также
отсутствие прямых операций оплаты в затронутых старых функциях.

- [ ] **Шаг 2: Перевести оставшиеся затронутые функции на `get_session()`**

Каждый объект либо используется внутри транзакции, либо превращается в обычный словарь до её
закрытия.

- [ ] **Шаг 3: Запустить тесты и зафиксировать**

```powershell
python -m unittest tests.test_no_global_session_in_core -v
git add bot services tests/test_no_global_session_in_core.py
git commit -m "Завершить переход ядра на короткие транзакции"
```

## Задача 13: Финальная проверка и публикация

**Файлы:**
- Проверить весь проект
- Обновить: `docs/superpowers/specs/2026-06-12-booking-core-reliability-design.md` только при обнаружении согласованных уточнений

- [ ] **Шаг 1: Запустить полную компиляцию**

```powershell
Get-ChildItem -Recurse -Filter *.py |
  Where-Object { $_.FullName -notmatch '\\deploy\\|\\backups\\|\\__pycache__\\' } |
  ForEach-Object { python -m py_compile $_.FullName }
```

- [ ] **Шаг 2: Запустить все тесты**

```powershell
python -m unittest discover -s tests -v
```

Ожидается: все тесты проходят без ошибок.

- [ ] **Шаг 3: Проверить импорты**

```powershell
python -c "import main; import handlers; import bot.routers.legacy; import bot.routers.admin_payments; import bot.routers.admin_booking_edit; print('imports ok')"
```

- [ ] **Шаг 4: Создать финальную резервную копию**

Создать ZIP проекта и резервную копию БД после реализации, проверить `PRAGMA integrity_check`.

- [ ] **Шаг 5: Опубликовать на GitHub и Bothost**

```powershell
git status --short
git push origin main
```

На Bothost выполнить `Обновить из Git`, дождаться статуса `Работает`, проверить рабочие и
сборочные логи.

- [ ] **Шаг 6: Выполнить ручной smoke-тест в Telegram**

Проверить:

```text
Создать тестовую заявку
Добавить частичную оплату и доплату
Перенести даты
Сменить проживание
Открыть карточки гостя и администратора
Запустить тестовые уведомления
Проверить статусы проживания
Проверить ежедневную сводку
Вызвать две одинаковые тестовые ошибки и проверить группировку
```

- [ ] **Шаг 7: Зафиксировать финальный коммит**

```powershell
git add .
git commit -m "Завершить надёжное ядро бронирования"
git push origin main
```
