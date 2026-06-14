# Admin Booking Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать подробную ежедневную сводку, полезные технические алерты, защиту от дублей и понятное админское управление заявками с редактированием и мягким удалением.

**Architecture:** Бизнес-правила размещаются в небольших сервисах с короткими SQLAlchemy-транзакциями. Telegram-роутеры показывают карточку, меню и подтверждения, а миграции безопасно добавляют поля мягкого удаления к существующей SQLite-базе.

**Tech Stack:** Python 3.12, aiogram 3, SQLAlchemy, SQLite, unittest, Bothost, GitHub.

---

### Task 1: Мягкое удаление и активные заявки

**Files:**
- Modify: `db/models.py`
- Modify: `db/migrations.py`
- Modify: `services/booking_management_service.py`
- Test: `tests/test_booking_management_service.py`
- Test: `tests/test_reliable_core_migrations.py`

- [ ] Добавить падающие тесты для колонок `deleted_at`, `deleted_by`, `deletion_reason`, мягкого удаления и восстановления.
- [ ] Запустить тесты и подтвердить ожидаемое падение.
- [ ] Добавить модель, миграцию и функции `soft_delete_booking`, `restore_booking`.
- [ ] Запустить тесты и подтвердить прохождение.

### Task 2: Подробная ежедневная сводка

**Files:**
- Modify: `services/daily_summary_service.py`
- Modify: `services/booking_card_service.py`
- Test: `tests/test_daily_summary_service.py`

- [ ] Добавить падающие тесты подробной карточки, итогов, предупреждений и исключения неактуальных заявок.
- [ ] Запустить тесты и подтвердить ожидаемое падение.
- [ ] Реализовать подробную сводку и человекочитаемые статусы.
- [ ] Запустить тесты и подтвердить прохождение.

### Task 3: Полезные алерты и защита от дублей

**Files:**
- Modify: `services/error_monitor_service.py`
- Modify: `services/notification_service.py`
- Modify: `services/booking_notification_service.py`
- Test: `tests/test_error_monitor_service.py`
- Test: `tests/test_notification_workflows.py`

- [ ] Добавить падающие тесты классификации временных ошибок, нормализованного fingerprint и совпадающего `chat_id`.
- [ ] Запустить тесты и подтвердить ожидаемое падение.
- [ ] Реализовать классификацию и объединение получателей.
- [ ] Запустить тесты и подтвердить прохождение.

### Task 4: Единое админское управление заявкой

**Files:**
- Modify: `bot/routers/admin_booking_edit.py`
- Modify: `bot/routers/legacy.py`
- Modify: `bot/states.py`
- Modify: `handlers.py`
- Test: `tests/test_booking_management_service.py`

- [ ] Добавить сервисные тесты изменения полей, допустимых переходов и истории.
- [ ] Реализовать отдельные кнопки этапа, проживания, редактирования полей и мягкого удаления.
- [ ] После каждого действия показывать обновлённую карточку.
- [ ] Запустить связанные тесты.

### Task 5: Проверка, Git и Bothost

**Files:**
- Modify: tracked project sources and docs

- [ ] Создать резервную копию базы и проверить `PRAGMA integrity_check`.
- [ ] Запустить компиляцию, полный набор тестов и проверку импортов.
- [ ] Синхронизировать проверенные исходники с git-репозиторием без `.env`, базы, логов и резервных копий.
- [ ] Просмотреть diff, создать коммит и отправить `main` в GitHub.
- [ ] В Bothost выполнить обновление из Git, дождаться статуса `Работает` и проверить свежие логи.
