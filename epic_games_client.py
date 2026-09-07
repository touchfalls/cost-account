"""
Epic Games API Client.

Asynchronous client for Epic Games services providing Device Code OAuth flow
and querying the Fortnite player Athena profile locker.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# Switch / PC client credentials header
BASIC_SWITCH_PC_AUTH: str = (
    "basic OThmN2U0MmMyZTNhNGY4NmE3NGViNDNmYmI0MWVkMzk6MGEyNDQ5YTItMDAxYS00NTFlLWFmZWMtM2U4MTI5MDFjNGQ3"
)

EPIC_OAUTH_DEVICE_AUTH_URL: str = (
    "https://account-public-service-prod.ol.epicgames.com/account/api/oauth/deviceAuthorization"
)
EPIC_OAUTH_TOKEN_URL: str = (
    "https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token"
)
EPIC_QUERY_PROFILE_URL: str = (
    "https://fngw-mcp-gc-livefn.ol.epicgames.com/fortnite/api/game/v2/profile/{account_id}/client/QueryProfile?profileId=athena&rvn=-1"
)
EPIC_ACCOUNT_PUBLIC_URL: str = (
    "https://account-public-service-prod.ol.epicgames.com/account/api/public/account/{account_id}"
)

# Target locker item templateId prefixes
DEFAULT_LOCKER_PREFIXES: tuple[str, ...] = (
    "AthenaCharacter",
    "AthenaPickaxe",
    "AthenaGlider",
    "AthenaBackpack",
    "AthenaDance",
)


# =====================================================================
# Custom Exceptions
# =====================================================================


class EpicAPIError(Exception):
    """Base exception for Epic Games API errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        numeric_error_code: Optional[int] = None,
        raw_response: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.numeric_error_code = numeric_error_code
        self.raw_response = raw_response


class AuthPendingError(EpicAPIError):
    """Raised when user has not yet authorized the device code."""


class AuthExpiredError(EpicAPIError):
    """Raised when the device code has expired or polling timed out."""


# =====================================================================
# Epic Games Client
# =====================================================================


class EpicGamesClient:
    """
    Asynchronous client for Epic Games Device Code authentication
    and Fortnite locker extraction.
    """

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        *,
        auth_header: str = BASIC_SWITCH_PC_AUTH,
        timeout: float = 30.0,
    ) -> None:
        """
        Initialize the Epic Games client.

        :param session: Optional existing aiohttp.ClientSession. If omitted, one is managed internally.
        :param auth_header: Basic authorization header for Switch/PC client.
        :param timeout: Request timeout in seconds.
        """
        self._external_session = session
        self._managed_session: Optional[aiohttp.ClientSession] = None
        self.auth_header = auth_header
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return the active session or create a managed one."""
        if self._external_session is not None and not self._external_session.closed:
            return self._external_session

        if self._managed_session is None or self._managed_session.closed:
            self._managed_session = aiohttp.ClientSession(timeout=self.timeout)

        return self._managed_session

    async def close(self) -> None:
        """Close the managed session if one was created."""
        if self._managed_session is not None and not self._managed_session.closed:
            await self._managed_session.close()

    async def __aenter__(self) -> "EpicGamesClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def get_client_credentials_token(self) -> str:
        """
        Obtain a client credentials access token for deviceAuthorization.

        :return: Access token string.
        :raises EpicAPIError: If the token request fails.
        """
        session = await self._get_session()
        headers = {
            "Authorization": self.auth_header,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}

        try:
            async with session.post(
                EPIC_OAUTH_TOKEN_URL,
                headers=headers,
                data=data,
            ) as response:
                payload = await response.json()
                if response.status != 200:
                    error_msg = payload.get("errorMessage", f"HTTP {response.status}")
                    error_code = payload.get("errorCode")
                    numeric_code = payload.get("numericErrorCode")
                    logger.error(
                        "Failed to obtain client credentials token: %s (%s)",
                        error_msg,
                        error_code,
                    )
                    raise EpicAPIError(
                        f"Failed to obtain client credentials token: {error_msg}",
                        status_code=response.status,
                        error_code=error_code,
                        numeric_error_code=numeric_code,
                        raw_response=payload,
                    )

                access_token = payload.get("access_token")
                if not access_token:
                    raise EpicAPIError(
                        "Client credentials response missing access_token",
                        status_code=response.status,
                        raw_response=payload,
                    )
                return str(access_token)
        except aiohttp.ClientError as exc:
            logger.error("Network error during client_credentials token request: %s", exc)
            raise EpicAPIError(f"Network error during client credentials request: {exc}") from exc

    async def create_device_code(self) -> dict[str, Any]:
        """
        Initiate the Device Code OAuth flow.

        First obtains a client credentials token, then requests device authorization.

        :return: Dictionary containing 'device_code', 'user_code',
                 'verification_uri_complete', 'interval', 'expires_in', etc.
        :raises EpicAPIError: If the API returns an error or unexpected status.
        """
        session = await self._get_session()
        client_token = await self.get_client_credentials_token()

        headers = {
            "Authorization": f"bearer {client_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        logger.info("Requesting Epic Games device code...")
        try:
            async with session.post(
                EPIC_OAUTH_DEVICE_AUTH_URL,
                headers=headers,
                data={"prompt": "login"},
            ) as response:
                payload = await response.json()
                if response.status != 200:
                    error_msg = payload.get("errorMessage", f"HTTP status {response.status}")
                    error_code = payload.get("errorCode")
                    numeric_code = payload.get("numericErrorCode")
                    logger.error("Failed to create device code: %s (%s)", error_msg, error_code)
                    raise EpicAPIError(
                        f"Failed to create device code: {error_msg}",
                        status_code=response.status,
                        error_code=error_code,
                        numeric_error_code=numeric_code,
                        raw_response=payload,
                    )

                logger.info(
                    "Device code created successfully. User code: %s",
                    payload.get("user_code"),
                )
                return payload
        except aiohttp.ClientError as exc:
            logger.error("Network error during create_device_code: %s", exc)
            raise EpicAPIError(f"Network error during device code creation: {exc}") from exc

    async def fetch_device_token(self, device_code: str) -> Tuple[str, str]:
        """
        Make a single poll attempt to exchange a device code for access tokens.

        :param device_code: Unique device authorization code.
        :return: Tuple of (access_token, account_id).
        :raises AuthPendingError: If the user hasn't authorized yet.
        :raises AuthExpiredError: If the device code expired.
        :raises EpicAPIError: If another API error occurred.
        """
        session = await self._get_session()
        headers = {
            "Authorization": self.auth_header,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "device_code",
            "device_code": device_code,
        }

        try:
            async with session.post(
                EPIC_OAUTH_TOKEN_URL,
                headers=headers,
                data=data,
            ) as response:
                payload = await response.json()

                if response.status == 200:
                    access_token = payload.get("access_token")
                    account_id = payload.get("account_id")
                    if not access_token or not account_id:
                        raise EpicAPIError(
                            "Token endpoint returned 200 but missing access_token or account_id",
                            status_code=response.status,
                            raw_response=payload,
                        )
                    return str(access_token), str(account_id)

                error_code = payload.get("errorCode") or payload.get("error", "")
                error_msg = payload.get("errorMessage") or payload.get("error_description", f"HTTP {response.status}")
                numeric_code = payload.get("numericErrorCode")

                if error_code in (
                    "errors.com.epicgames.account.device_authorization_pending",
                    "errors.com.epicgames.account.oauth.authorization_pending",
                    "authorization_pending"
                ):
                    raise AuthPendingError(
                        error_msg,
                        status_code=response.status,
                        error_code=error_code,
                        numeric_error_code=numeric_code,
                        raw_response=payload,
                    )

                if error_code in (
                    "errors.com.epicgames.account.device_code_expired",
                    "expired_token"
                ):
                    raise AuthExpiredError(
                        error_msg,
                        status_code=response.status,
                        error_code=error_code,
                        numeric_error_code=numeric_code,
                        raw_response=payload,
                    )

                raise EpicAPIError(
                    f"Token request failed: {error_msg}. Raw payload: {payload}",
                    status_code=response.status,
                    error_code=error_code,
                    numeric_error_code=numeric_code,
                    raw_response=payload,
                )
        except aiohttp.ClientError as exc:
            logger.error("Network error during token poll: %s", exc)
            raise EpicAPIError(f"Network error during token polling: {exc}") from exc

    async def poll_device_token(
        self,
        device_code: str,
        interval: int = 10,
        timeout: int = 300,
    ) -> Tuple[str, str]:
        """
        Asynchronously poll the token endpoint until authorization is granted or times out.

        :param device_code: Device code obtained from `create_device_code`.
        :param interval: Sleep interval in seconds between poll attempts.
        :param timeout: Maximum wait time in seconds before raising AuthExpiredError.
        :return: Tuple of (access_token, account_id).
        :raises AuthExpiredError: If the device code expired or timeout is reached.
        :raises EpicAPIError: If any unrecoverable Epic Games API error occurs.
        """
        start_time = asyncio.get_running_loop().time()
        logger.info(
            "Polling device token (interval=%ds, timeout=%ds)...",
            interval,
            timeout,
        )

        while (asyncio.get_running_loop().time() - start_time) < timeout:
            try:
                tokens = await self.fetch_device_token(device_code)
                logger.info("Successfully acquired access token for account: %s", tokens[1])
                return tokens
            except AuthPendingError:
                logger.debug("Authorization pending. Waiting %d seconds...", interval)
                await asyncio.sleep(interval)
            except AuthExpiredError:
                logger.warning("Device code has expired on the server.")
                raise

        raise AuthExpiredError(
            f"Device authorization timed out after {timeout} seconds.",
            error_code="errors.com.epicgames.account.device_code_expired",
        )

    async def get_account_info(
        self,
        account_id: str,
        access_token: str,
    ) -> dict[str, Any]:
        """
        Fetch public account information (including country and displayName).
        """
        session = await self._get_session()
        url = EPIC_ACCOUNT_PUBLIC_URL.format(account_id=account_id)
        headers = {
            "Authorization": f"bearer {access_token}",
        }

        logger.info("Fetching account info for %s...", account_id)
        try:
            async with session.get(url, headers=headers) as response:
                payload = await response.json()

                if response.status != 200:
                    error_msg = payload.get("errorMessage", f"HTTP {response.status}")
                    error_code = payload.get("errorCode")
                    numeric_code = payload.get("numericErrorCode")
                    logger.error("get_account_info failed: %s (%s)", error_msg, error_code)
                    raise EpicAPIError(
                        f"Failed to query account info: {error_msg}",
                        status_code=response.status,
                        error_code=error_code,
                        numeric_error_code=numeric_code,
                        raw_response=payload,
                    )
                return payload
        except aiohttp.ClientError as exc:
            logger.error("Network error during get_account_info: %s", exc)
            raise EpicAPIError(f"Network error during get_account_info: {exc}") from exc

    async def get_locker_items(
        self,
        account_id: str,
        access_token: str,
        *,
        target_prefixes: Sequence[str] = DEFAULT_LOCKER_PREFIXES,
    ) -> list[str]:
        """
        Retrieve all cosmetic locker items from the player's Fortnite Athena profile.

        Sends a POST request with an empty JSON object to the QueryProfile endpoint,
        filters items having matching templateId prefixes, and extracts clean IDs.

        :param account_id: Player's Epic Games account ID.
        :param access_token: Valid Bearer access token.
        :param target_prefixes: Cosmetic categories to extract
                                (e.g. AthenaCharacter, AthenaPickaxe, AthenaGlider,
                                 AthenaBackpack, AthenaDance).
        :return: List of clean UPPERCASE cosmetic IDs
                 (e.g. 'CID_028_ATHENA_COMMANDO_M_SCRAPPER').
        :raises EpicAPIError: If the profile request fails or response format is unexpected.
        """
        session = await self._get_session()
        url = EPIC_QUERY_PROFILE_URL.format(account_id=account_id)
        headers = {
            "Authorization": f"bearer {access_token}",
            "Content-Type": "application/json",
        }

        logger.info("Fetching Athena profile for account %s...", account_id)
        try:
            async with session.post(url, headers=headers, json={}) as response:
                payload = await response.json()

                if response.status != 200:
                    error_msg = payload.get("errorMessage", f"HTTP {response.status}")
                    error_code = payload.get("errorCode")
                    numeric_code = payload.get("numericErrorCode")
                    logger.error("QueryProfile failed: %s (%s)", error_msg, error_code)
                    raise EpicAPIError(
                        f"Failed to query Athena profile: {error_msg}",
                        status_code=response.status,
                        error_code=error_code,
                        numeric_error_code=numeric_code,
                        raw_response=payload,
                    )
        except aiohttp.ClientError as exc:
            logger.error("Network error during QueryProfile: %s", exc)
            raise EpicAPIError(f"Network error during QueryProfile: {exc}") from exc

        # Extract profile items dictionary
        profile_changes = payload.get("profileChanges")
        if not isinstance(profile_changes, list) or not profile_changes:
            logger.error("QueryProfile response missing 'profileChanges' array")
            raise EpicAPIError(
                "Invalid QueryProfile response: 'profileChanges' is missing or empty",
                raw_response=payload,
            )

        profile_data = profile_changes[0].get("profile", {})
        raw_items = profile_data.get("items", {})
        if not isinstance(raw_items, dict):
            logger.warning("Profile 'items' is not a dictionary: %s", type(raw_items))
            return []

        clean_ids: list[str] = []
        normalized_prefixes = tuple(p.lower() for p in target_prefixes)

        for item_key, item_val in raw_items.items():
            template_id = ""
            if isinstance(item_val, dict):
                template_id = str(item_val.get("templateId") or "")
            elif isinstance(item_val, str):
                template_id = item_val

            if not template_id:
                continue

            # Check if templateId matches any of the target prefixes
            # e.g. AthenaCharacter:cid_028_athena_commando_m_scrapper
            if ":" in template_id:
                prefix, _, raw_id = template_id.partition(":")
                if prefix.lower() in normalized_prefixes:
                    cleaned = raw_id.strip().upper()
                    if cleaned:
                        clean_ids.append(cleaned)
            else:
                # Handle prefixes without colon if any
                for prefix_str in target_prefixes:
                    if template_id.lower().startswith(prefix_str.lower()):
                        raw_id = template_id[len(prefix_str) :].lstrip(":")
                        cleaned = raw_id.strip().upper()
                        if cleaned:
                            clean_ids.append(cleaned)
                        break

        # Deduplicate while preserving insertion order
        unique_clean_ids = list(dict.fromkeys(clean_ids))

        logger.info(
            "Extracted %d unique cosmetic IDs for account %s",
            len(unique_clean_ids),
            account_id,
        )
        return unique_clean_ids
