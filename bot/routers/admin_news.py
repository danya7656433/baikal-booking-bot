import logging
import traceback
from datetime import datetime
from typing import Union

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest

from bot.states import NewsStates
from config import ADMIN_CHAT_ID
from database import AdminLog, News, User, session

router = Router()

@router.callback_query(F.data == "admin_create_news")
async def admin_create_news(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    await state.update_data(previous_menu="admin")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]]
    )
    await callback.message.edit_text(
        "📰 Введите текст новости для рассылки всем пользователям:", reply_markup=kb
    )
    await state.set_state(NewsStates.waiting_for_news_content)
    session.add(
        AdminLog(admin_id=callback.from_user.id, action="Начал создание новости")
    )
    session.commit()
    logging.info(f"Админ {callback.from_user.id} начал создание новости")
    await callback.answer()


@router.message(NewsStates.waiting_for_news_content)
async def process_news_content(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет прав на это действие.")
        return

    try:
        content = message.text.strip()
        if not content:
            await message.answer(
                "⚠️ Текст новости не может быть пустым. Введите текст:"
            )
            return

        # Сохраняем черновик новости
        news = News(admin_id=message.from_user.id, content=content, status="draft")
        session.add(news)
        session.commit()

        await state.update_data(news_id=news.id, news_content=content)

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⏭ Пропустить медиа",
                        callback_data=f"news_skip_media_{news.id}",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")],
            ]
        )
        await message.answer(
            "📸 Прикрепите одно фото или видео для новости или нажмите 'Пропустить медиа':",
            reply_markup=kb,
        )
        await state.set_state(NewsStates.waiting_for_news_media)
        session.add(
            AdminLog(
                admin_id=message.from_user.id,
                action=f"Создал черновик новости #{news.id}",
            )
        )
        session.commit()
        logging.info(f"Админ {message.from_user.id} создал черновик новости #{news.id}")
    except Exception as e:
        logging.error(
            f"Ошибка в process_news_content: {str(e)}\n{traceback.format_exc()}"
        )
        await message.answer(f"❗ Ошибка: {str(e)}")
        await state.set_state(NewsStates.waiting_for_news_content)


@router.message(F.photo | F.video, NewsStates.waiting_for_news_media)
@router.callback_query(F.data.startswith("news_skip_media_"))
@router.callback_query(F.data.startswith("news_keep_media_"))
async def process_news_media(
    update: Union[Message, CallbackQuery], state: FSMContext, bot: Bot
):
    if str(update.from_user.id) != str(ADMIN_CHAT_ID):
        if isinstance(update, CallbackQuery):
            await update.answer("У вас нет прав на это действие", show_alert=True)
        else:
            await update.answer("У вас нет прав на это действие.")
        return

    try:
        data = await state.get_data()
        news_id = data.get("news_id")
        news = session.query(News).filter_by(id=news_id, status="draft").first()
        if not news:
            if isinstance(update, CallbackQuery):
                await update.answer(
                    "Новость не найдена или уже отправлена.", show_alert=True
                )
            else:
                await update.answer("Новость не найдена или уже отправлена.")
            await state.clear()
            return

        # Определяем, что делать с медиа
        if isinstance(update, Message):
            if update.photo:
                photo_id = update.photo[-1].file_id
                news.photo_id = photo_id
                news.video_id = None
                session.commit()
                await state.update_data(news_media_id=photo_id, news_media_type="photo")
                logging.info(
                    f"Админ {update.from_user.id} прикрепил фото к новости #{news_id}"
                )
            elif update.video:
                video_id = update.video.file_id
                # Проверяем размер видео (например, не больше 20 МБ для Telegram)
                if update.video.file_size > 20 * 1024 * 1024:
                    await update.answer(
                        "⚠️ Видео слишком большое (более 20 МБ). Загрузите видео меньшего размера."
                    )
                    return
                news.video_id = video_id
                news.photo_id = None
                session.commit()
                await state.update_data(news_media_id=video_id, news_media_type="video")
                logging.info(
                    f"Админ {update.from_user.id} прикрепил видео к новости #{news_id}"
                )
            else:
                await update.answer("⚠️ Пожалуйста, отправьте фото или видео.")
                return
        elif isinstance(update, CallbackQuery) and update.data.startswith(
            "news_skip_media_"
        ):
            news.photo_id = None
            news.video_id = None
            session.commit()
            await state.update_data(news_media_id=None, news_media_type=None)
            logging.info(
                f"Админ {update.from_user.id} пропустил прикрепление медиа к новости #{news_id}"
            )
        elif isinstance(update, CallbackQuery) and update.data.startswith(
            "news_keep_media_"
        ):
            # Оставляем текущее медиа без изменений
            media_id = news.video_id if news.video_id else news.photo_id
            media_type = (
                "video" if news.video_id else "photo" if news.photo_id else None
            )
            await state.update_data(news_media_id=media_id, news_media_type=media_type)
            logging.info(
                f"Админ {update.from_user.id} оставил текущее медиа ({media_type}) для новости #{news_id}"
            )
        else:
            if isinstance(update, Message):
                await update.answer(
                    "⚠️ Пожалуйста, отправьте фото или видео или нажмите 'Пропустить медиа'."
                )
            return

        # Показываем предпросмотр
        content = news.content
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Отправить", callback_data=f"news_send_{news.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✏️ Редактировать", callback_data=f"news_edit_{news.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить", callback_data=f"news_cancel_{news.id}"
                    )
                ],
            ]
        )

        if isinstance(update, CallbackQuery):
            chat_id = update.message.chat.id
            if news.video_id:
                await bot.send_video(
                    chat_id=chat_id,
                    video=news.video_id,
                    caption=f"📰 Предпросмотр новости:\n\n{content}\n\nВыберите действие:",
                    reply_markup=kb,
                )
                await update.message.delete()
            elif news.photo_id:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=news.photo_id,
                    caption=f"📰 Предпросмотр новости:\n\n{content}\n\nВыберите действие:",
                    reply_markup=kb,
                )
                await update.message.delete()
            else:
                await update.message.edit_text(
                    f"📰 Предпросмотр новости:\n\n{content}\n\nВыберите действие:",
                    reply_markup=kb,
                )
        else:
            if news.video_id:
                await bot.send_video(
                    chat_id=update.chat.id,
                    video=news.video_id,
                    caption=f"📰 Предпросмотр новости:\n\n{content}\n\nВыберите действие:",
                    reply_markup=kb,
                )
            elif news.photo_id:
                await bot.send_photo(
                    chat_id=update.chat.id,
                    photo=news.photo_id,
                    caption=f"📰 Предпросмотр новости:\n\n{content}\n\nВыберите действие:",
                    reply_markup=kb,
                )
            else:
                await update.answer(
                    f"📰 Предпросмотр новости:\n\n{content}\n\nВыберите действие:",
                    reply_markup=kb,
                )

        await state.set_state(NewsStates.waiting_for_news_action)
        session.add(
            AdminLog(
                admin_id=update.from_user.id,
                action=f"Обработал медиа для новости #{news.id}",
            )
        )
        session.commit()
    except Exception as e:
        logging.error(
            f"Ошибка в process_news_media: {str(e)}\n{traceback.format_exc()}"
        )
        if isinstance(update, CallbackQuery):
            await update.message.edit_text(
                f"❗ Ошибка: {str(e)}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                    ]
                ),
            )
            await update.answer()
        else:
            await update.answer(f"❗ Ошибка: {str(e)}")
        await state.set_state(NewsStates.waiting_for_news_media)


@router.callback_query(F.data.startswith("news_send_"))
async def send_news(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    try:
        news_id = int(callback.data.split("_")[2])
        news = session.query(News).filter_by(id=news_id, status="draft").first()
        if not news:
            await callback.answer(
                "Новость не найдена или уже отправлена.", show_alert=True
            )
            return

        # Получаем всех пользователей, принявших соглашение
        users = session.query(User).filter_by(rules_accepted=True).all()
        sent_count = 0
        failed_count = 0

        for user in users:
            try:
                if news.video_id:
                    await callback.message.bot.send_video(
                        chat_id=user.user_id,
                        video=news.video_id,
                        caption=f"📰 Новость от Дача на Байкале:\n\n{news.content}",
                        parse_mode="Markdown",
                    )
                elif news.photo_id:
                    await callback.message.bot.send_photo(
                        chat_id=user.user_id,
                        photo=news.photo_id,
                        caption=f"📰 Новость от Дача на Байкале:\n\n{news.content}",
                        parse_mode="Markdown",
                    )
                else:
                    await callback.message.bot.send_message(
                        chat_id=user.user_id,
                        text=f"📰 Новость от Дача на Байкале:\n\n{news.content}",
                        parse_mode="Markdown",
                    )
                sent_count += 1
            except Exception as e:
                logging.error(
                    f"Ошибка при отправке новости пользователю {user.user_id}: {str(e)}"
                )
                failed_count += 1

        news.status = "sent"
        news.sent_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        session.commit()

        # Удаляем сообщение предпросмотра
        try:
            await callback.message.delete()
        except TelegramBadRequest as e:
            logging.warning(f"Не удалось удалить сообщение предпросмотра: {str(e)}")

        # Отправляем новое сообщение с результатом
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
            ]
        )
        await callback.message.answer(
            f"✅ Новость отправлена!\n"
            f"Успешно: {sent_count} пользователям\n"
            f"Не удалось: {failed_count} пользователям",
            reply_markup=kb,
        )

        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Отправил новость #{news_id} ({sent_count} успех, {failed_count} неудач)",
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} отправил новость #{news_id}: {sent_count} успех, {failed_count} неудач"
        )
        await state.clear()
    except Exception as e:
        logging.error(f"Ошибка в send_news: {str(e)}\n{traceback.format_exc()}")
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
            ]
        )
        await callback.message.answer(
            f"❗ Ошибка при отправке новости: {str(e)}", reply_markup=kb
        )
    await callback.answer()


@router.callback_query(F.data.startswith("news_edit_"))
async def edit_news(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    try:
        news_id = int(callback.data.split("_")[2])
        news = session.query(News).filter_by(id=news_id, status="draft").first()
        if not news:
            await callback.answer(
                "Новость не найдена или уже отправлена.", show_alert=True
            )
            return

        await state.update_data(news_id=news_id)
        media_status = (
            "Видео прикреплено"
            if news.video_id
            else "Фото прикреплено" if news.photo_id else "Медиа отсутствует"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад", callback_data=f"news_preview_{news_id}"
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            f"✏️ Текущий текст новости:\n\n{news.content}\n\n"
            f"📸 Текущее медиа: {media_status}\n\n"
            f"Введите новый текст (или отправьте /keep, чтобы сохранить текущий):",
            reply_markup=kb,
        )
        await state.set_state(NewsStates.waiting_for_news_edit)
        logging.info(
            f"Админ {callback.from_user.id} начал редактирование новости #{news_id}"
        )
    except Exception as e:
        logging.error(f"Ошибка в edit_news: {str(e)}\n{traceback.format_exc()}")
        await callback.message.edit_text(
            f"❗ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            ),
        )
    await callback.answer()


@router.message(NewsStates.waiting_for_news_edit)
async def process_news_edit(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет прав на это действие.")
        return

    try:
        data = await state.get_data()
        news_id = data.get("news_id")
        news = session.query(News).filter_by(id=news_id, status="draft").first()
        if not news:
            await message.answer("Новость не найдена или уже отправлена.")
            await state.clear()
            return

        content = message.text.strip()
        if content == "/keep":
            content = news.content
        elif not content:
            await message.answer(
                "⚠️ Текст новости не может быть пустым. Введите текст или /keep:"
            )
            return

        news.content = content
        session.commit()
        await state.update_data(news_content=content)

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⏭ Пропустить медиа",
                        callback_data=f"news_skip_media_{news.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📷 Оставить текущее медиа",
                        callback_data=f"news_keep_media_{news.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад", callback_data=f"news_preview_{news_id}"
                    )
                ],
            ]
        )
        await message.answer(
            f"📸 Прикрепите новое фото или видео для новости, нажмите 'Пропустить медиа' для удаления медиа или 'Оставить текущее медиа':",
            reply_markup=kb,
        )
        await state.set_state(NewsStates.waiting_for_news_media)
        session.add(
            AdminLog(
                admin_id=message.from_user.id,
                action=f"Отредактировал текст новости #{news.id}",
            )
        )
        session.commit()
        logging.info(
            f"Админ {message.from_user.id} отредактировал текст новости #{news.id}"
        )
    except Exception as e:
        logging.error(f"Ошибка в process_news_edit: {str(e)}\n{traceback.format_exc()}")
        await message.answer(f"❗ Ошибка: {str(e)}")
        await state.set_state(NewsStates.waiting_for_news_edit)


@router.callback_query(F.data.startswith("news_preview_"))
async def preview_news(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    try:
        news_id = int(callback.data.split("_")[2])
        news = session.query(News).filter_by(id=news_id, status="draft").first()
        if not news:
            await callback.answer(
                "Новость не найдена или уже отправлена.", show_alert=True
            )
            return

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Отправить", callback_data=f"news_send_{news.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✏️ Редактировать", callback_data=f"news_edit_{news.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить", callback_data=f"news_cancel_{news.id}"
                    )
                ],
            ]
        )

        if news.video_id:
            await callback.message.bot.send_video(
                chat_id=callback.message.chat.id,
                video=news.video_id,
                caption=f"📰 Предпросмотр новости:\n\n{news.content}\n\nВыберите действие:",
                reply_markup=kb,
            )
            await callback.message.delete()
        elif news.photo_id:
            await callback.message.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=news.photo_id,
                caption=f"📰 Предпросмотр новости:\n\n{news.content}\n\nВыберите действие:",
                reply_markup=kb,
            )
            await callback.message.delete()
        else:
            await callback.message.edit_text(
                f"📰 Предпросмотр новости:\n\n{news.content}\n\nВыберите действие:",
                reply_markup=kb,
            )

        await state.set_state(NewsStates.waiting_for_news_action)
        logging.info(
            f"Админ {callback.from_user.id} вернулся к предпросмотру новости #{news_id}"
        )
    except Exception as e:
        logging.error(f"Ошибка в preview_news: {str(e)}\n{traceback.format_exc()}")
        await callback.message.edit_text(
            f"❗ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("news_cancel_"))
async def cancel_news(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    try:
        news_id = int(callback.data.split("_")[2])
        news = session.query(News).filter_by(id=news_id, status="draft").first()
        if not news:
            await callback.answer(
                "Новость не найдена или уже отправлена.", show_alert=True
            )
            return

        news.status = "cancelled"
        session.commit()

        await callback.message.edit_text(
            f"❌ Создание новости #{news_id} отменено.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            ),
        )
        session.add(
            AdminLog(
                admin_id=callback.from_user.id, action=f"Отменил новость #{news_id}"
            )
        )
        session.commit()
        logging.info(f"Админ {callback.from_user.id} отменил новость #{news_id}")
        await state.clear()
    except Exception as e:
        logging.error(f"Ошибка в cancel_news: {str(e)}\n{traceback.format_exc()}")
        await callback.message.edit_text(
            f"❗ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            ),
        )
    await callback.answer()
