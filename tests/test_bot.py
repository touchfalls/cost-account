"""Unit tests for bot module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, User, Chat

from cosmetics_service import CosmeticDatabase, CosmeticItem
from epic_games_client import AuthExpiredError, EpicAPIError, EpicGamesClient
from evaluator import CategorySummary, LockerEvaluation, LockerEvaluator
import bot as bot_module
from bot import (
    _process_user_locker,
    active_tasks,
    active_users,
    handle_cancel_auth,
    handle_command_start,
    handle_start_auth,
    on_shutdown,
    on_startup,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure clean active_users and active_tasks state for every test."""
    active_users.clear()
    active_tasks.clear()
    yield
    active_users.clear()
    active_tasks.clear()


@pytest.fixture
def mock_bot() -> AsyncMock:
    bot = AsyncMock(spec=Bot)
    bot.edit_message_text = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.fixture
def mock_epic_client() -> AsyncMock:
    client = AsyncMock(spec=EpicGamesClient)
    client.create_device_code = AsyncMock(
        return_value={
            "device_code": "mock_device_code_123",
            "user_code": "ABCD",
            "verification_uri_complete": "https://www.epicgames.com/activate?userCode=ABCD",
            "expires_in": 600,
            "interval": 10,
        }
    )
    client.poll_device_token = AsyncMock(
        return_value=("mock_access_token", "mock_account_id_456")
    )
    client.get_locker_items = AsyncMock(
        return_value=["CID_001", "PICKAXE_001"]
    )
    return client


@pytest.fixture
def mock_cosmetics_db() -> MagicMock:
    db = MagicMock(spec=CosmeticDatabase)
    db.get_item = MagicMock(
        return_value=CosmeticItem.model_validate(
            {"id": "CID_001", "name": "Galaxy", "type": "skin", "price": 2000}
        )
    )
    return db


@pytest.fixture
def mock_evaluator() -> MagicMock:
    evaluator = MagicMock(spec=LockerEvaluator)
    evaluator.evaluate = MagicMock(
        return_value=LockerEvaluation(
            total_vbucks=2000,
            total_items=1,
            paid_items_count=1,
            exclusive_or_bp_count=0,
            unindexed_items_count=0,
            categories={"AthenaCharacter": CategorySummary(count=1, total_vbucks=2000)},
            top_valuable_items=[
                CosmeticItem.model_validate(
                    {"id": "CID_001", "name": "Galaxy", "type": "skin", "price": 2000}
                )
            ],
        )
    )
    return evaluator


@pytest.mark.asyncio
async def test_handle_command_start():
    """Verify /start sends welcome message with start keyboard."""
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()

    await handle_command_start(message)

    message.answer.assert_awaited_once()
    args, kwargs = message.answer.call_args
    assert "Добро пожаловать в калькулятор шкафчика" in args[0]
    assert kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_handle_start_auth_anti_spam(
    mock_bot: AsyncMock,
    mock_epic_client: AsyncMock,
    mock_cosmetics_db: MagicMock,
    mock_evaluator: MagicMock,
):
    """Verify that repeated clicks while active trigger an alert and do not spawn a new session."""
    user = User(id=111, is_bot=False, first_name="TestUser")
    message = AsyncMock(spec=Message)
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = user
    callback.message = message
    callback.answer = AsyncMock()

    # Mark user as already active
    active_users.add(111)

    await handle_start_auth(
        callback=callback,
        bot=mock_bot,
        epic_client=mock_epic_client,
        cosmetics_db=mock_cosmetics_db,
        evaluator=mock_evaluator,
    )

    callback.answer.assert_awaited_once()
    assert callback.answer.call_args.kwargs.get("show_alert") is True
    assert "У вас уже есть активная сессия" in callback.answer.call_args.args[0]
    # Ensure create_device_code was NOT called
    mock_epic_client.create_device_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_start_auth_success(
    mock_bot: AsyncMock,
    mock_epic_client: AsyncMock,
    mock_cosmetics_db: MagicMock,
    mock_evaluator: MagicMock,
):
    """Verify normal start_auth flow sets active_user, edits message, and starts worker task."""
    user = User(id=222, is_bot=False, first_name="Player2")
    chat = Chat(id=999, type="private")
    message = AsyncMock(spec=Message)
    message.chat = chat
    message.message_id = 777
    message.edit_text = AsyncMock()

    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = user
    callback.message = message
    callback.answer = AsyncMock()

    with patch("bot.asyncio.create_task") as mock_create_task:
        mock_task = MagicMock(spec=asyncio.Task)

        def mock_spawn(coro):
            coro.close()
            return mock_task

        mock_create_task.side_effect = mock_spawn

        await handle_start_auth(
            callback=callback,
            bot=mock_bot,
            epic_client=mock_epic_client,
            cosmetics_db=mock_cosmetics_db,
            evaluator=mock_evaluator,
        )

        assert 222 in active_users
        mock_epic_client.create_device_code.assert_awaited_once()
        message.edit_text.assert_awaited_once()
        args, kwargs = message.edit_text.call_args
        assert "Авторизация в Epic Games" in args[0]
        assert "ABCD" in args[0]
        assert kwargs.get("reply_markup") is not None
        mock_create_task.assert_called_once()
        assert active_tasks.get(222) == mock_task


@pytest.mark.asyncio
async def test_handle_cancel_auth():
    """Verify cancellation cancels the task, removes user from active set, and updates message."""
    user = User(id=333, is_bot=False, first_name="Player3")
    message = AsyncMock(spec=Message)
    message.edit_text = AsyncMock()

    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = user
    callback.message = message
    callback.data = "cancel_auth:333"
    callback.answer = AsyncMock()

    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.done.return_value = False
    active_users.add(333)
    active_tasks[333] = mock_task

    await handle_cancel_auth(callback)

    mock_task.cancel.assert_called_once()
    assert 333 not in active_users
    assert 333 not in active_tasks
    callback.answer.assert_awaited_once_with("Сессия авторизации отменена.")
    message.edit_text.assert_awaited_once()
    assert "Авторизация отменена" in message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_process_user_locker_success(
    mock_bot: AsyncMock,
    mock_epic_client: AsyncMock,
    mock_cosmetics_db: MagicMock,
    mock_evaluator: MagicMock,
):
    """Verify worker task succeeds, formats report, edits message, and clears active_users."""
    user_id = 444
    active_users.add(user_id)

    await _process_user_locker(
        user_id=user_id,
        chat_id=1001,
        message_id=2001,
        device_code="dev_code_1",
        interval=5,
        timeout=60,
        display_name="ProPlayer",
        bot=mock_bot,
        epic_client=mock_epic_client,
        cosmetics_db=mock_cosmetics_db,
        evaluator=mock_evaluator,
    )

    # Polling, items fetching, evaluation
    mock_epic_client.poll_device_token.assert_awaited_once_with(
        device_code="dev_code_1",
        interval=5,
        timeout=60,
    )
    mock_epic_client.get_locker_items.assert_awaited_once_with(
        account_id="mock_account_id_456",
        access_token="mock_access_token",
    )
    mock_evaluator.evaluate.assert_called_once()

    # edit_message_text was called at least twice (status update + final report)
    assert mock_bot.edit_message_text.await_count >= 2
    last_call_args = mock_bot.edit_message_text.call_args.kwargs
    assert "ProPlayer" in last_call_args["text"]
    assert "2 000 V-Bucks" in last_call_args["text"]

    # Lock must be released
    assert user_id not in active_users


@pytest.mark.asyncio
async def test_process_user_locker_auth_expired(
    mock_bot: AsyncMock,
    mock_epic_client: AsyncMock,
    mock_cosmetics_db: MagicMock,
    mock_evaluator: MagicMock,
):
    """Verify handling of device code expiration."""
    user_id = 555
    active_users.add(user_id)
    mock_epic_client.poll_device_token.side_effect = AuthExpiredError("Expired")

    await _process_user_locker(
        user_id=user_id,
        chat_id=1001,
        message_id=2001,
        device_code="dev_code_exp",
        interval=5,
        timeout=60,
        display_name="Player",
        bot=mock_bot,
        epic_client=mock_epic_client,
        cosmetics_db=mock_cosmetics_db,
        evaluator=mock_evaluator,
    )

    last_call_args = mock_bot.edit_message_text.call_args.kwargs
    assert "Время ожидания авторизации истекло" in last_call_args["text"]
    assert user_id not in active_users


@pytest.mark.asyncio
async def test_process_user_locker_epic_api_error(
    mock_bot: AsyncMock,
    mock_epic_client: AsyncMock,
    mock_cosmetics_db: MagicMock,
    mock_evaluator: MagicMock,
):
    """Verify handling of Epic API error."""
    user_id = 666
    active_users.add(user_id)
    mock_epic_client.poll_device_token.side_effect = EpicAPIError("API down")

    await _process_user_locker(
        user_id=user_id,
        chat_id=1001,
        message_id=2001,
        device_code="dev_code_err",
        interval=5,
        timeout=60,
        display_name="Player",
        bot=mock_bot,
        epic_client=mock_epic_client,
        cosmetics_db=mock_cosmetics_db,
        evaluator=mock_evaluator,
    )

    last_call_args = mock_bot.edit_message_text.call_args.kwargs
    assert "Временные неполадки на стороне сервисов Epic Games" in last_call_args["text"]
    assert user_id not in active_users


@pytest.mark.asyncio
async def test_lifespan_startup_and_shutdown():
    """Verify on_startup and on_shutdown manage the aiohttp ClientSession properly."""
    dp = Dispatcher()
    bot = AsyncMock(spec=Bot)

    with patch.object(CosmeticDatabase, "load_cosmetics", new=AsyncMock()):
        await on_startup(dp, bot)

        session = dp.get("aiohttp_session")
        assert session is not None
        assert not session.closed
        assert "cosmetics_db" in dp.workflow_data
        assert "epic_client" in dp.workflow_data
        assert "evaluator" in dp.workflow_data

        await on_shutdown(dp, bot)
        assert session.closed
