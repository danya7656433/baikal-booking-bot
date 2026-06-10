# Развертывание на Bothost

1. Создать Telegram-бота в панели Bothost и загрузить код проекта.
2. Выбрать тариф с постоянным хранилищем. SQLite нельзя использовать на бесплатном тарифе, если данные должны сохраняться.
3. Через файловый менеджер загрузить актуальную базу как `/app/data/booking.db`.
4. Добавить переменные окружения:
   - `BOT_TOKEN`
   - `ADMIN_CHAT_ID`
   - `ADMIN_USERNAME`
   - `PHOTO_STORAGE_CHAT_ID`
   - `DATA_DIR=/app/data`
   - `DATABASE_PATH=/app/data/booking.db`
5. Команда запуска: `python main.py`.
6. Перед запуском остановить локальную копию бота, иначе Telegram long polling будет запущен одновременно в двух местах.
