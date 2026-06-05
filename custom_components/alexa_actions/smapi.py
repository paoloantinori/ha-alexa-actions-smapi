"""SMAPI client for automated Alexa custom skill management via Lambda ARN."""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from homeassistant.exceptions import HomeAssistantError

from .exceptions import SMAPIError

from .api import LWAClient
from .const import DEFAULT_SKILL_NAME, SMAPI_BASE_URL

_LOGGER = logging.getLogger(__name__)

_EN_MANIFEST_INFO = {
    "summary": "Actionable notifications from Home Assistant.",
    "description": (
        "Trigger actionable notifications on your Alexa devices"
        " from Home Assistant."
    ),
    "keywords": ["notification", "home automation", "actionable"],
}

_FR_MANIFEST_INFO = {
    "summary": "Notifications interactives de Home Assistant.",
    "description": (
        "Déclenche des notifications interactives sur vos appareils Alexa"
        " depuis Home Assistant."
    ),
    "keywords": ["notification", "domotique", "interactive"],
}

_MANIFEST_LOCALE_INFO: dict[str, dict] = {
    "de-DE": {
        "summary": "Interaktive Benachrichtigungen von Home Assistant.",
        "description": (
            "Löst interaktive Benachrichtigungen auf Ihren Alexa-Geräten"
            " über Home Assistant aus."
        ),
        "keywords": ["Benachrichtigung", "Hausautomation", "interaktiv"],
    },
    "en-GB": _EN_MANIFEST_INFO,
    "en-US": _EN_MANIFEST_INFO,
    "es-ES": {
        "summary": "Notificaciones interactivas de Home Assistant.",
        "description": (
            "Activa notificaciones interactivas en sus dispositivos Alexa"
            " desde Home Assistant."
        ),
        "keywords": ["notificación", "domótica", "interactiva"],
    },
    "fr-CA": _FR_MANIFEST_INFO,
    "fr-FR": _FR_MANIFEST_INFO,
    "it-IT": {
        "summary": "Notifiche interattive da Home Assistant.",
        "description": (
            "Attiva notifiche interattive sui tuoi dispositivi Alexa"
            " da Home Assistant."
        ),
        "keywords": ["notifica", "domotica", "interattiva"],
    },
    "pt-BR": {
        "summary": "Notificações interativas do Home Assistant.",
        "description": (
            "Aciona notificações interativas em seus dispositivos Alexa"
            " pelo Home Assistant."
        ),
        "keywords": ["notificação", "automação residencial", "interativa"],
    },
}

_MAX_UPLOAD_RETRIES = 2


class SMAPI:
    """Manages Alexa custom skill lifecycle through the SMAPI REST API."""

    def __init__(self, lwa_client: LWAClient) -> None:
        self._lwa_client = lwa_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def async_get_vendor_id(self) -> str:
        """Return the first vendor ID from the Amazon developer account."""
        data = await self._async_request("GET", "/v1/vendors")
        if not isinstance(data, dict):
            raise SMAPIError("Unexpected response from vendor list API")
        vendors = data.get("vendors", [])
        if not vendors:
            raise SMAPIError("No Amazon vendor account found")
        return vendors[0]["id"]

    async def async_update_manifest(
        self,
        skill_id: str,
        lambda_arn: str,
        skill_name: str = DEFAULT_SKILL_NAME,
        locales: list[str] | None = None,
    ) -> None:
        """Replace the skill manifest, pointing at the given Lambda ARN."""
        manifest = self._build_manifest(lambda_arn, skill_name, locales)
        await self._async_request(
            "PUT",
            f"/v1/skills/{skill_id}/stages/development/manifest",
            json={"manifest": manifest},
            headers={"If-Match": "*"},
        )

    async def async_upload_model(
        self,
        skill_id: str,
        locale: str,
        model: dict,
    ) -> None:
        """Upload an interaction model for a single locale.

        Fetches the current model's ETag first so the PUT is idempotent.
        """
        headers: dict[str, str] = {}
        try:
            existing = await self._async_request(
                "GET",
                f"/v1/skills/{skill_id}/stages/development"
                f"/interactionModel/locales/{locale}",
            )
            if isinstance(existing, dict) and "eTag" in existing:
                headers["If-Match"] = existing["eTag"]
        except HomeAssistantError:
            pass

        await self._async_request(
            "PUT",
            f"/v1/skills/{skill_id}/stages/development"
            f"/interactionModel/locales/{locale}",
            json=model,
            headers=headers,
        )

    async def async_enable_skill(self, skill_id: str) -> None:
        """Enable the skill for the development stage."""
        await self._async_request(
            "PUT",
            f"/v1/skills/{skill_id}/stages/development/enablement",
            json={},
        )

    async def async_get_skill_status(self, skill_id: str) -> dict:
        """Return the raw skill status payload from SMAPI."""
        data = await self._async_request(
            "GET", f"/v1/skills/{skill_id}/status"
        )
        return data if isinstance(data, dict) else {}

    async def async_wait_for_model_build(
        self,
        skill_id: str,
        locales: list[str],
        timeout: float = 60.0,
        poll_interval: float = 5.0,
    ) -> list[str]:
        """Poll skill status until at least one locale build reaches SUCCEEDED."""
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                data = await self.async_get_skill_status(skill_id)
            except HomeAssistantError as err:
                _LOGGER.warning(
                    "Skill status poll failed (will retry): %s", err
                )
                await asyncio.sleep(poll_interval)
                continue

            locale_statuses = (
                data.get("interactionModel", {}).get("locales", {})
            )
            succeeded: list[str] = []
            failed: list[str] = []
            pending: list[str] = []

            for locale in locales:
                status = (
                    locale_statuses.get(locale, {})
                    .get("lastUpdateRequest", {})
                    .get("status", "")
                )
                if status == "SUCCEEDED":
                    succeeded.append(locale)
                elif status == "FAILED":
                    failed.append(locale)
                else:
                    pending.append(locale)

            _LOGGER.info(
                "Skill %s build status — succeeded: %s, failed: %s, pending: %s",
                skill_id,
                succeeded,
                failed,
                pending,
            )

            if succeeded:
                return succeeded
            if not pending:
                break
            elapsed = deadline - time.monotonic()
            if elapsed > poll_interval:
                await asyncio.sleep(poll_interval)
            else:
                break

        raise SMAPIError(
            f"Model build timed out after {timeout}s"
            " — no locales reached SUCCEEDED"
        )

    async def async_setup_skill_complete(
        self,
        lambda_arn: str,
        models: dict[str, dict],
        skill_name: str = DEFAULT_SKILL_NAME,
    ) -> dict:
        """Orchestrate full skill setup.

        Steps:
        1. Get vendor ID.
        2. Find or create a development skill.
        3. Upload interaction models concurrently (with retries).
        4. Update the manifest for the locales that uploaded successfully.
        5. Enable the skill for development.

        Returns a dict with skill_id, vendor_id, and lambda_arn.
        """
        _LOGGER.info(
            "SMAPI setup: lambda_arn=%s, skill_name=%s",
            lambda_arn,
            skill_name,
        )

        vendor_id = await self.async_get_vendor_id()
        selected_locales = list(models.keys())
        skill_id: str | None = None

        # Reuse existing skill if one with a matching name already exists.
        skill_id = await self._async_find_existing_skill(vendor_id, skill_name)
        if skill_id:
            _LOGGER.info("Reusing existing skill: %s", skill_id)
        else:
            try:
                skill_id = await self._async_create_skill(
                    vendor_id, lambda_arn, skill_name, selected_locales
                )
                _LOGGER.info(
                    "Created new skill %s, waiting for provisioning", skill_id
                )
                await asyncio.sleep(5)
            except HomeAssistantError as err:
                if "409" in str(err):
                    skill_id = await self._async_resolve_conflict(
                        vendor_id, lambda_arn, skill_name
                    )
                else:
                    raise

        # Upload models concurrently with retry per locale.
        async def _upload(locale: str, model: dict) -> str | None:
            for attempt in range(_MAX_UPLOAD_RETRIES + 1):
                try:
                    await self.async_upload_model(
                        skill_id, model=model, locale=locale
                    )
                    return locale
                except HomeAssistantError as err:
                    if attempt < _MAX_UPLOAD_RETRIES:
                        _LOGGER.debug(
                            "Model upload for %s failed (attempt %d),"
                            " retrying: %s",
                            locale,
                            attempt + 1,
                            err,
                        )
                        await asyncio.sleep(2)
                    else:
                        _LOGGER.warning(
                            "Failed to upload model for %s after %d attempts:"
                            " %s",
                            locale,
                            _MAX_UPLOAD_RETRIES + 1,
                            err,
                        )
                        return None

        results = await asyncio.gather(
            *(_upload(loc, m) for loc, m in models.items())
        )
        upload_locales = [r for r in results if r is not None]

        if not upload_locales:
            raise SMAPIError(
                "All model uploads failed — no usable locales for skill"
            )

        # Update manifest with the locales that uploaded successfully.
        try:
            manifest = self._build_manifest(
                lambda_arn, skill_name, upload_locales
            )
            await self._async_request(
                "PUT",
                f"/v1/skills/{skill_id}/stages/development/manifest",
                json={"manifest": manifest},
                headers={"If-Match": "*"},
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Failed to update manifest (non-fatal): %s", err)

        # Enable the skill for development.
        try:
            await self.async_enable_skill(skill_id)
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Failed to enable skill %s"
                " (will need manual enable in Alexa developer console): %s",
                skill_id,
                err,
            )

        return {
            "skill_id": skill_id,
            "vendor_id": vendor_id,
            "lambda_arn": lambda_arn,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _async_create_skill(
        self,
        vendor_id: str,
        lambda_arn: str,
        skill_name: str,
        locales: list[str] | None = None,
    ) -> str:
        """Create a new custom skill and return its skill ID."""
        manifest = self._build_manifest(lambda_arn, skill_name, locales)
        data = await self._async_request(
            "POST",
            "/v1/skills",
            json={"vendorId": vendor_id, "manifest": manifest},
        )
        if not isinstance(data, dict) or "skillId" not in data:
            raise SMAPIError(
                "Skill creation did not return a skill ID"
            )
        return data["skillId"]

    async def _async_find_existing_skill(
        self, vendor_id: str, skill_name: str
    ) -> str | None:
        """Return the skill ID of an existing development skill matching *skill_name*."""
        data = await self._async_request(
            "GET", "/v1/skills", params={"vendorId": vendor_id}
        )
        if not isinstance(data, dict):
            return None
        for skill in data.get("skills", []):
            if skill.get("stage") != "development":
                continue
            skill_id = skill.get("skillId")
            name_by_locale = skill.get("nameByLocale", {})
            if any(
                v.get("name") == skill_name
                for v in name_by_locale.values()
                if isinstance(v, dict)
            ):
                _LOGGER.debug("Found matching development skill: %s", skill_id)
                return skill_id
        return None

    async def _async_resolve_conflict(
        self, vendor_id: str, lambda_arn: str, skill_name: str
    ) -> str:
        """Handle a 409 conflict by adopting the first existing skill."""
        data = await self._async_request(
            "GET", "/v1/skills", params={"vendorId": vendor_id}
        )
        if not isinstance(data, dict):
            raise SMAPIError(
                "Skill conflict but no existing skills found"
            )
        skills = data.get("skills", [])
        if not skills:
            raise SMAPIError(
                "Skill conflict but no existing skills found"
            )
        skill_id = skills[0]["skillId"]
        await self.async_update_manifest(skill_id, lambda_arn, skill_name)
        return skill_id

    def _build_manifest(
        self,
        lambda_arn: str,
        skill_name: str,
        locales: list[str] | None = None,
    ) -> dict:
        """Build the skill manifest with a Lambda ARN endpoint."""
        target = locales if locales else list(_MANIFEST_LOCALE_INFO)
        locale_manifests: dict[str, dict] = {}
        for loc in target:
            info = _MANIFEST_LOCALE_INFO.get(loc, _EN_MANIFEST_INFO)
            locale_manifests[loc] = {
                "name": skill_name,
                "examplePhrases": [f"Alexa, open {skill_name}"],
                **info,
            }

        return {
            "publishingInformation": {
                "locales": locale_manifests,
                "isAvailableWorldwide": True,
                "testingInstructions": (
                    f"Say 'Alexa, open {skill_name}'."
                    " Check for actionable notification."
                ),
            },
            "apis": {
                "custom": {
                    "endpoint": {
                        "sourceArn": lambda_arn,
                    },
                    "interfaces": [],
                }
            },
            "manifestVersion": "1.0",
        }

    async def _async_request(
        self, method: str, path: str, **kwargs
    ) -> dict | None:
        """Execute an authenticated SMAPI HTTP request."""
        token = await self._lwa_client.async_get_smapi_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        session = await self._lwa_client.get_session()
        url = f"{SMAPI_BASE_URL}{path}"
        try:
            async with session.request(
                method, url, headers=headers, **kwargs
            ) as resp:
                if resp.status == 401:
                    body = await resp.text()
                    _LOGGER.error(
                        "SMAPI 401 Unauthorized: %s %s — body: %s",
                        method,
                        path,
                        body[:500],
                    )
                    raise SMAPIError(
                        f"Invalid LWA credentials: {body[:200]}"
                    )
                if resp.status == 409:
                    text = await resp.text()
                    raise SMAPIError(f"Conflict (409): {text}")
                if resp.status == 204:
                    return None
                if resp.status >= 400:
                    body = await resp.text()
                    _LOGGER.error(
                        "SMAPI %s %s returned %s: %s",
                        method,
                        path,
                        resp.status,
                        body[:500],
                    )
                    raise SMAPIError(
                        f"SMAPI error ({resp.status}): {body[:200]}"
                    )
                return await resp.json()
        except HomeAssistantError:
            raise
        except aiohttp.ClientError as err:
            raise SMAPIError(
                f"SMAPI request failed: {err}"
            ) from err

