"""
Fortnite Locker Evaluator Telegram Bot.

Main entry point integrating cosmetics_service, epic_games_client, and evaluator
using aiogram 3.x, aiohttp, and python-dotenv.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from typing import Optional

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

from cosmetics_service import CosmeticDatabase
from epic_games_client import (
    AuthExpiredError,
    EpicAPIError,
    EpicGamesClient,
)
from evaluator import LockerEvaluation, LockerEvaluator, format_telegram_report

logger = logging.getLogger(__name__)

# State tracking for anti-spam and task lifecycle
active_users: set[int] = set()
active_tasks: dict[int, asyncio.Task[None]] = {}

router = Router(name="locker_evaluator_router")


# =====================================================================
# Keyboards
# =====================================================================


def get_start_keyboard() -> InlineKeyboardMarkup:
    """Return the initial inline keyboard with start evaluation button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 Оценить мой шкафчик",
                    callback_data="start_auth",
                )
            ]
        ]
    )


def get_auth_keyboard(auth_url: str, user_id: int) -> InlineKeyboardMarkup:
    """Return inline keyboard containing Epic Games auth URL and cancel button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Подтвердить вход в Epic Games",
                    url=auth_url,
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"cancel_auth:{user_id}",
                )
            ],
        ]
    )


def get_retry_keyboard() -> InlineKeyboardMarkup:
    """Return inline keyboard with retry button after completion or error."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Оценить снова",
                    callback_data="start_auth",
                )
            ]
        ]
    )


# =====================================================================
# Background Worker: Auth & Locker Evaluation
# =====================================================================


async def _process_user_locker(
    user_id: int,
    chat_id: int,
    message_id: int,
    device_code: str,
    interval: int,
    timeout: int,
    display_name: Optional[str],
    bot: Bot,
    epic_client: EpicGamesClient,
    cosmetics_db: CosmeticDatabase,
    evaluator: LockerEvaluator,
) -> None:
    """
    Background worker polling Epic Games Device Code and calculating locker value.

    Guarantees user session lock release in the finally block.
    """
    retry_kb = get_retry_keyboard()

    try:
        # 1. Poll for OAuth access token
        logger.info("Starting token poll for user %d (timeout=%ds)...", user_id, timeout)
        access_token, account_id = await epic_client.poll_device_token(
            device_code=device_code,
            interval=interval,
            timeout=timeout,
        )

        # 2. Update status in Telegram
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    "✅ <b>Авторизация успешна!</b>\n\n"
                    "🔍 Сканирую шкафчик и считаю стоимость предметов..."
                ),
            )
        except TelegramBadRequest:
            pass

        # 3. Fetch account info for country and display name
        logger.info("Fetching account public info for %s...", account_id)
        account_info = await epic_client.get_account_info(
            account_id=account_id,
            access_token=access_token,
        )
        account_country = account_info.get("country", "US")
        actual_display_name = account_info.get("displayName") or display_name
        
        # 4. Fetch locker items from Epic Games Athena profile
        logger.info("Fetching Athena profile items for account %s...", account_id)
        raw_items = await epic_client.get_locker_items(
            account_id=account_id,
            access_token=access_token,
        )

        # 5. Evaluate locker using CosmeticDatabase
        logger.info(
            "Evaluating %d raw items for account %s (user %d)...",
            len(raw_items),
            account_id,
            user_id,
        )
        evaluation: LockerEvaluation = evaluator.evaluate(
            raw_item_ids=raw_items,
            db=cosmetics_db,
            account_country=account_country
        )

        # 6. Format Telegram HTML report
        report_html = format_telegram_report(
            evaluation=evaluation,
            display_name=actual_display_name,
        )

        # 6. Send evaluation report to user
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=report_html,
                reply_markup=retry_kb,
            )
        except TelegramBadRequest:
            await bot.send_message(
                chat_id=chat_id,
                text=report_html,
                reply_markup=retry_kb,
            )

        logger.info(
            "Locker evaluation for user %d completed: %d V-Bucks across %d items",
            user_id,
            evaluation.valuation_result.known_shop_value_vbucks,
            evaluation.total_items,
        )

    except asyncio.CancelledError:
        logger.info("Auth task for user %d was cancelled.", user_id)
        raise

    except AuthExpiredError:
        logger.warning("Device code expired or polling timed out for user %d.", user_id)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    "⏰ <b>Время ожидания авторизации истекло.</b>\n\n"
                    "Ссылка действительна ограниченное время. Пожалуйста, попробуйте снова."
                ),
                reply_markup=retry_kb,
            )
        except TelegramBadRequest:
            pass

    except EpicAPIError as exc:
        logger.error("Epic Games API error for user %d: %s", user_id, exc)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    "⚠️ <b>Временные неполадки на стороне сервисов Epic Games.</b>\n\n"
                    "Не удалось завершить операцию. Пожалуйста, повторите попытку через пару минут."
                ),
                reply_markup=retry_kb,
            )
        except TelegramBadRequest:
            pass

    except aiohttp.ClientError as exc:
        logger.error("Network communication error for user %d: %s", user_id, exc)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    "📡 <b>Ошибка сети при обращении к серверам.</b>\n\n"
                    "Проверьте соединение или попробуйте немного позже."
                ),
                reply_markup=retry_kb,
            )
        except TelegramBadRequest:
            pass

    except Exception as exc:
        logger.exception(
            "Unexpected error while processing locker for user %d: %s",
            user_id,
            exc,
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    "❌ <b>Произошла непредвиденная ошибка при обработке шкафчика.</b>\n\n"
                    "Пожалуйста, попробуйте снова."
                ),
                reply_markup=retry_kb,
            )
        except TelegramBadRequest:
            pass

    finally:
        active_users.discard(user_id)
        active_tasks.pop(user_id, None)


# =====================================================================
# Router Handlers
# =====================================================================


@router.message(CommandStart())
async def handle_command_start(message: Message) -> None:
    """Handle /start command with introduction and evaluation button."""
    welcome_text = (
        "👋 <b>Добро пожаловать в калькулятор шкафчика Fortnite!</b>\n\n"
        "Этот бот позволяет узнать точную стоимость всех ваших скинов, кирок, "
        "дельтапланов и эмоций в <b>V-Bucks</b>, а также увидеть топ самых редких и ценных предметов.\n\n"
        "🔒 <b>Безопасность:</b>\n"
        "Авторизация происходит через официальный механизм <b>Epic Games Device Code Flow</b>.\n"
        "Вам <b>не нужно</b> передавать боту свои логины и пароли — вход подтверждается "
        "напрямую на официальном сайте Epic Games.\n\n"
        "Нажмите кнопку ниже, чтобы начать:"
    )
    await message.answer(welcome_text, reply_markup=get_start_keyboard())


@router.callback_query(F.data == "start_auth")
async def handle_start_auth(
    callback: CallbackQuery,
    bot: Bot,
    epic_client: EpicGamesClient,
    cosmetics_db: CosmeticDatabase,
    evaluator: LockerEvaluator,
) -> None:
    """
    Handle user requesting account evaluation.

    Creates Device Code, updates message with verification link, and spawns background worker.
    Protects against spam and parallel sessions.
    """
    user_id = callback.from_user.id

    if not isinstance(callback.message, Message):
        await callback.answer(
            "⚠️ Сообщение недоступно. Пожалуйста, отправьте /start снова.",
            show_alert=True,
        )
        return

    target_message = callback.message

    # Anti-spam / concurrent session check
    if user_id in active_users:
        await callback.answer(
            "⚠️ У вас уже есть активная сессия авторизации!\n"
            "Подтвердите вход по ссылке или дождитесь завершения таймера.",
            show_alert=True,
        )
        return

    active_users.add(user_id)
    await callback.answer()

    # Request device code from Epic Games
    try:
        device_data = await epic_client.create_device_code()
    except Exception as exc:
        active_users.discard(user_id)
        logger.error("Failed to generate device code for user %d: %s", user_id, exc)
        await target_message.answer(
            "⚠️ Не удалось получить ссылку для авторизации от Epic Games. "
            "Пожалуйста, попробуйте снова через минуту.",
            reply_markup=get_retry_keyboard(),
        )
        return

    device_code = device_data.get("device_code", "")
    auth_url = (
        device_data.get("verification_uri_complete")
        or device_data.get("verification_uri")
        or "https://www.epicgames.com/activate"
    )
    user_code = device_data.get("user_code", "")
    expires_in = int(device_data.get("expires_in", 600))
    interval = int(device_data.get("interval", 10))
    expires_minutes = max(1, expires_in // 60)

    auth_message_text = (
        "🔑 <b>Авторизация в Epic Games</b>\n\n"
        "Для оценки шкафчика перейдите по кнопке ниже и подтвердите вход в аккаунт.\n\n"
        f"Код устройства: <code>{html.escape(user_code)}</code>\n\n"
        "⏳ <b>Ожидаю подтверждения входа...</b>\n"
        f"<i>Ссылка действительна {expires_minutes} мин.</i>"
    )

    auth_keyboard = get_auth_keyboard(auth_url=auth_url, user_id=user_id)

    try:
        await target_message.edit_text(auth_message_text, reply_markup=auth_keyboard)
        message_id = target_message.message_id
        chat_id = target_message.chat.id
    except TelegramBadRequest:
        sent_msg = await target_message.answer(auth_message_text, reply_markup=auth_keyboard)
        message_id = sent_msg.message_id
        chat_id = sent_msg.chat.id

    display_name = callback.from_user.full_name or callback.from_user.username

    # Spawn background task for token polling & analysis
    worker_task = asyncio.create_task(
        _process_user_locker(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            device_code=device_code,
            interval=interval,
            timeout=expires_in,
            display_name=display_name,
            bot=bot,
            epic_client=epic_client,
            cosmetics_db=cosmetics_db,
            evaluator=evaluator,
        )
    )
    active_tasks[user_id] = worker_task


@router.callback_query(F.data.startswith("cancel_auth:"))
async def handle_cancel_auth(callback: CallbackQuery) -> None:
    """Handle user cancelling the ongoing device code session."""
    data = callback.data or ""
    try:
        target_user_id = int(data.split(":")[1])
    except (IndexError, ValueError):
        target_user_id = callback.from_user.id

    if callback.from_user.id != target_user_id:
        await callback.answer(
            "Это действие предназначено для другого пользователя.",
            show_alert=True,
        )
        return

    # Cancel background worker task if still running
    task = active_tasks.pop(target_user_id, None)
    if task and not task.done():
        task.cancel()

    active_users.discard(target_user_id)
    await callback.answer("Сессия авторизации отменена.")

    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                "❌ <b>Авторизация отменена.</b>\n\n"
                "Вы можете начать оценку шкафчика в любое удобное время.",
                reply_markup=get_start_keyboard(),
            )
        except TelegramBadRequest:
            pass


# =====================================================================
# Lifespan Management (Startup & Shutdown)
# =====================================================================


async def on_startup(dispatcher: Dispatcher, bot: Bot) -> None:
    """
    Initialize singletons, persistent aiohttp ClientSession, and load cosmetics catalog.
    """
    logger.info("Initializing bot lifespan services...")

    session = aiohttp.ClientSession()
    cosmetics_db = CosmeticDatabase.get_instance()

    logger.info("Prefetching and caching Fortnite cosmetics catalog...")
    try:
        await cosmetics_db.load_cosmetics(session)
        logger.info("Successfully loaded %d items into database.", len(cosmetics_db))
    except Exception as exc:
        logger.warning(
            "Initial cosmetics load encountered an issue: %s. "
            "Bot will continue and attempt fallback/runtime lookups.",
            exc,
        )

    epic_client = EpicGamesClient(session=session)
    evaluator = LockerEvaluator()

    # Register dependencies into dispatcher workflow_data for automatic handler injection
    dispatcher["aiohttp_session"] = session
    dispatcher["cosmetics_db"] = cosmetics_db
    dispatcher["epic_client"] = epic_client
    dispatcher["evaluator"] = evaluator

    logger.info("Bot startup completed successfully.")


async def on_shutdown(dispatcher: Dispatcher, bot: Bot) -> None:
    """
    Gracefully terminate all running background tasks and close the aiohttp session.
    """
    logger.info("Initiating bot shutdown sequence...")

    # Cancel all active user authorization tasks
    for task in list(active_tasks.values()):
        if not task.done():
            task.cancel()
    active_tasks.clear()
    active_users.clear()

    # Close shared aiohttp.ClientSession
    session: Optional[aiohttp.ClientSession] = dispatcher.get("aiohttp_session")
    if session is not None and not session.closed:
        await session.close()
        await asyncio.sleep(0.01)
        logger.info("Shared aiohttp.ClientSession closed cleanly.")

    logger.info("Bot shutdown completed.")


# =====================================================================
# Main Application Entry Point
# =====================================================================


async def main() -> None:
    """Main application runner."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN")

    if not bot_token or bot_token == "your_telegram_bot_token_here":
        logger.error(
            "CRITICAL: BOT_TOKEN is not set or contains placeholder value! "
            "Please create a .env file and set BOT_TOKEN=<your_token>."
        )
        return

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Register lifespan handlers
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Register routes
    dp.include_router(router)

    logger.info("Starting Telegram Bot long-polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
