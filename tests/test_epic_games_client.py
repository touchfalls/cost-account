"""Unit tests for EpicGamesClient."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from epic_games_client import (
    BASIC_SWITCH_PC_AUTH,
    AuthExpiredError,
    AuthPendingError,
    EpicAPIError,
    EpicGamesClient,
)


@pytest.fixture
def mock_session():
    session = MagicMock(spec=aiohttp.ClientSession)
    session.closed = False
    return session


class TestEpicGamesClientDeviceAuth:
    @pytest.mark.asyncio
    async def test_create_device_code_success(self, mock_session):
        expected_data = {
            "user_code": "ABC123",
            "device_code": "dev_code_xyz",
            "verification_uri": "https://www.epicgames.com/id/activate",
            "verification_uri_complete": "https://www.epicgames.com/id/activate?userCode=ABC123",
            "prompt": "Enter the code",
            "expires_in": 600,
            "interval": 5,
        }

        # First call: client_credentials token
        mock_resp_token = AsyncMock()
        mock_resp_token.status = 200
        mock_resp_token.json = AsyncMock(return_value={"access_token": "mock_client_token_123"})

        # Second call: deviceAuthorization
        mock_resp_device = AsyncMock()
        mock_resp_device.status = 200
        mock_resp_device.json = AsyncMock(return_value=expected_data)

        mock_session.post.return_value.__aenter__.side_effect = [
            mock_resp_token,
            mock_resp_device,
        ]

        client = EpicGamesClient(session=mock_session)
        result = await client.create_device_code()

        assert result == expected_data
        assert result["device_code"] == "dev_code_xyz"
        assert result["user_code"] == "ABC123"
        assert result["verification_uri_complete"] == "https://www.epicgames.com/id/activate?userCode=ABC123"
        assert result["interval"] == 5
        assert result["expires_in"] == 600

        # Check call arguments
        assert mock_session.post.call_count == 2
        calls = mock_session.post.call_args_list
        assert calls[0].kwargs["headers"]["Authorization"] == BASIC_SWITCH_PC_AUTH
        assert calls[1].kwargs["headers"]["Authorization"] == "bearer mock_client_token_123"

    @pytest.mark.asyncio
    async def test_create_device_code_api_error(self, mock_session):
        mock_resp_token = AsyncMock()
        mock_resp_token.status = 200
        mock_resp_token.json = AsyncMock(return_value={"access_token": "mock_client_token_123"})

        mock_resp_device = AsyncMock()
        mock_resp_device.status = 400
        mock_resp_device.json = AsyncMock(
            return_value={
                "errorCode": "errors.com.epicgames.bad_request",
                "errorMessage": "Invalid client",
            }
        )

        mock_session.post.return_value.__aenter__.side_effect = [
            mock_resp_token,
            mock_resp_device,
        ]

        client = EpicGamesClient(session=mock_session)
        with pytest.raises(EpicAPIError) as exc_info:
            await client.create_device_code()

        assert "Invalid client" in str(exc_info.value)
        assert exc_info.value.status_code == 400


class TestEpicGamesClientPolling:
    @pytest.mark.asyncio
    async def test_poll_device_token_immediate_success(self, mock_session):
        token_payload = {
            "access_token": "eg1~access_token_123",
            "account_id": "account_abc_999",
            "token_type": "bearer",
        }
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=token_payload)
        mock_session.post.return_value.__aenter__.return_value = mock_resp

        client = EpicGamesClient(session=mock_session)
        access_token, account_id = await client.poll_device_token(
            device_code="dev_code_xyz",
            interval=1,
            timeout=5,
        )

        assert access_token == "eg1~access_token_123"
        assert account_id == "account_abc_999"

    @pytest.mark.asyncio
    async def test_poll_device_token_pending_then_success(self, mock_session):
        pending_resp = AsyncMock()
        pending_resp.status = 400
        pending_resp.json = AsyncMock(
            return_value={
                "errorCode": "errors.com.epicgames.account.device_authorization_pending",
                "errorMessage": "Waiting for user",
            }
        )

        success_resp = AsyncMock()
        success_resp.status = 200
        success_resp.json = AsyncMock(
            return_value={
                "access_token": "eg1~valid_token",
                "account_id": "acc_12345",
            }
        )

        cm_pending = MagicMock()
        cm_pending.__aenter__ = AsyncMock(return_value=pending_resp)
        cm_pending.__aexit__ = AsyncMock(return_value=None)

        cm_success = MagicMock()
        cm_success.__aenter__ = AsyncMock(return_value=success_resp)
        cm_success.__aexit__ = AsyncMock(return_value=None)

        mock_session.post.side_effect = [cm_pending, cm_success]

        client = EpicGamesClient(session=mock_session)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            access_token, account_id = await client.poll_device_token(
                device_code="dev_code_xyz",
                interval=2,
                timeout=10,
            )

            assert access_token == "eg1~valid_token"
            assert account_id == "acc_12345"
            mock_sleep.assert_awaited_once_with(2)

    @pytest.mark.asyncio
    async def test_poll_device_token_expired_error(self, mock_session):
        expired_resp = AsyncMock()
        expired_resp.status = 400
        expired_resp.json = AsyncMock(
            return_value={
                "errorCode": "errors.com.epicgames.account.device_code_expired",
                "errorMessage": "Device code has expired",
            }
        )
        mock_session.post.return_value.__aenter__.return_value = expired_resp

        client = EpicGamesClient(session=mock_session)
        with pytest.raises(AuthExpiredError) as exc_info:
            await client.poll_device_token("dev_code_xyz", interval=1, timeout=5)

        assert "Device code has expired" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_poll_device_token_timeout(self, mock_session):
        pending_resp = AsyncMock()
        pending_resp.status = 400
        pending_resp.json = AsyncMock(
            return_value={
                "errorCode": "errors.com.epicgames.account.device_authorization_pending",
                "errorMessage": "Pending",
            }
        )
        mock_session.post.return_value.__aenter__.return_value = pending_resp

        client = EpicGamesClient(session=mock_session)
        with pytest.raises(AuthExpiredError) as exc_info:
            # Poll with timeout 0 seconds so loop terminates immediately
            await client.poll_device_token("dev_code_xyz", interval=1, timeout=0)

        assert "timed out" in str(exc_info.value).lower()


class TestEpicGamesClientLocker:
    @pytest.mark.asyncio
    async def test_get_locker_items_success(self, mock_session):
        profile_response = {
            "profileRevision": 55,
            "profileChanges": [
                {
                    "changeType": "fullProfileUpdate",
                    "profile": {
                        "items": {
                            "item_guid_1": {
                                "templateId": "AthenaCharacter:cid_028_athena_commando_m_scrapper",
                                "quantity": 1,
                            },
                            "item_guid_2": {
                                "templateId": "AthenaPickaxe:pickaxe_lockjaw",
                                "quantity": 1,
                            },
                            "item_guid_3": {
                                "templateId": "AthenaGlider:glider_default",
                                "quantity": 1,
                            },
                            "item_guid_4": {
                                "templateId": "AthenaBackpack:bid_backpack_mirror",
                                "quantity": 1,
                            },
                            "item_guid_5": {
                                "templateId": "AthenaDance:eid_floss",
                                "quantity": 1,
                            },
                            # Non-locker item (should be filtered out)
                            "item_guid_6": {
                                "templateId": "Token:season_token_01",
                                "quantity": 5,
                            },
                            "item_guid_7": {
                                "templateId": "Quest:daily_quest_123",
                                "quantity": 1,
                            },
                        }
                    },
                }
            ],
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=profile_response)
        mock_session.post.return_value.__aenter__.return_value = mock_resp

        client = EpicGamesClient(session=mock_session)
        items = await client.get_locker_items(
            account_id="test_account_id",
            access_token="test_bearer_token",
        )

        assert items == [
            "CID_028_ATHENA_COMMANDO_M_SCRAPPER",
            "PICKAXE_LOCKJAW",
            "GLIDER_DEFAULT",
            "BID_BACKPACK_MIRROR",
            "EID_FLOSS",
        ]

        # Verify POST request parameters
        mock_session.post.assert_called_once()
        args, kwargs = mock_session.post.call_args
        assert "test_account_id" in args[0]
        assert kwargs["headers"]["Authorization"] == "bearer test_bearer_token"
        assert kwargs["json"] == {}

    @pytest.mark.asyncio
    async def test_get_locker_items_api_failure(self, mock_session):
        mock_resp = AsyncMock()
        mock_resp.status = 401
        mock_resp.json = AsyncMock(
            return_value={
                "errorCode": "errors.com.epicgames.common.authentication.token_verification_failed",
                "errorMessage": "Token invalid",
            }
        )
        mock_session.post.return_value.__aenter__.return_value = mock_resp

        client = EpicGamesClient(session=mock_session)
        with pytest.raises(EpicAPIError) as exc_info:
            await client.get_locker_items("account_id", "invalid_token")

        assert "Token invalid" in str(exc_info.value)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with EpicGamesClient() as client:
            session = await client._get_session()
            assert not session.closed

        assert session.closed
