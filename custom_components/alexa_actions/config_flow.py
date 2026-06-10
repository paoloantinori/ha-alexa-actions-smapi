"""Config flow for the Alexa Actions SMAPI integration."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import LWAClient
from .exceptions import SMAPIError
from .const import (
    CONF_HA_TOKEN,
    CONF_HA_URL,
    CONF_INVOCATION_NAME,
    CONF_LOCALES,
    CONF_REFRESH_TOKEN,
    CONF_SKILL_ID,
    CONF_VENDOR_ID,
    DEFAULT_SKILL_NAME,
    DOMAIN,
    SCOPE_SMAPI,
)
from .models import LOCALE_LABELS, get_model
from .smapi import SMAPI
from .views import AlexaAuthCallbackView

_LOGGER = logging.getLogger(__name__)

_LOCALE_OPTIONS: list[SelectOptionDict] = [
    SelectOptionDict(value=locale, label=label)
    for locale, label in LOCALE_LABELS.items()
]

_DEFAULT_LOCALES = ["en-US"]

_CALLBACK_PATH = "/auth/alexa_actions/callback"
_SKILL_WEBHOOK_PATH = "/api/alexa_actions/skill"


class AlexaActionsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Alexa Actions SMAPI."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._lwa_client: LWAClient | None = None
        self._auth_state: str | None = None
        self._user_input: dict[str, Any] = {}

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AlexaActionsOptionsFlow:
        """Return the options flow handler."""
        return AlexaActionsOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Collect LWA and Home Assistant credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                # Detect HA URL if not explicitly provided.
                if not user_input.get(CONF_HA_URL):
                    try:
                        user_input[CONF_HA_URL] = get_url(self.hass)
                    except NoURLAvailableError:
                        errors[CONF_HA_URL] = "unknown"
                        raise

                # Set unique ID based on LWA client ID.
                await self.async_set_unique_id(user_input[CONF_CLIENT_ID])
                self._abort_if_unique_id_configured()

                # Create the LWA client to validate credentials.
                lwa_client = LWAClient(
                    self.hass,
                    user_input[CONF_CLIENT_ID],
                    user_input[CONF_CLIENT_SECRET],
                )
                self._lwa_client = lwa_client
                self._user_input = user_input

                # Register the callback view.
                self._register_callback_view()

                return await self.async_step_auth_smapi()

            except AbortFlow:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error in user step")
                errors["base"] = "unknown"

        # Build default HA URL for the form.
        ha_url_default = ""
        try:
            ha_url_default = get_url(self.hass)
        except NoURLAvailableError:
            pass

        data_schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required(CONF_CLIENT_SECRET): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_HA_URL, default=ha_url_default): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Required(CONF_HA_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(
                    CONF_INVOCATION_NAME, default=DEFAULT_SKILL_NAME
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_LOCALES, default=_DEFAULT_LOCALES
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=_LOCALE_OPTIONS,
                        mode=SelectSelectorMode.LIST,
                        multiple=True,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_auth_smapi(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Redirect user to LWA OAuth2 authorization."""
        if self._lwa_client is None:
            return await self.async_step_user()

        errors: dict[str, str] = {}

        # If user_input is provided, it means they clicked "Submit" after
        # authorizing.  Try to find the auth code using the *existing* state.
        if user_input is not None and self._auth_state:
            callback_url = self._get_callback_url()
            auth_codes = self.hass.data.get(DOMAIN, {}).get("auth_codes", {})
            code = auth_codes.pop(self._auth_state, None)

            _LOGGER.debug(
                "auth_smapi submit: state=%s, code_found=%s, "
                "stored_states=%s, callback_url=%s",
                self._auth_state,
                code is not None,
                list(auth_codes.keys()),
                callback_url,
            )

            if code:
                try:
                    token_data = await self._lwa_client.async_exchange_code(
                        code=code,
                        redirect_uri=callback_url,
                        scope=SCOPE_SMAPI,
                    )
                    # Store refresh token for later use.
                    refresh_token = token_data.get("refresh_token", "")
                    self._user_input[CONF_REFRESH_TOKEN] = refresh_token
                    _LOGGER.info("LWA code exchange succeeded")
                    return await self.async_step_setup()
                except HomeAssistantError as err:
                    _LOGGER.error("LWA code exchange failed: %s", err)
                    errors["base"] = "invalid_auth"
            else:
                _LOGGER.warning(
                    "Auth code not found for state=%s. "
                    "Ensure the callback URL is reachable from Amazon.",
                    self._auth_state,
                )
                errors["base"] = "authorization_pending"

        # Build callback URL and auth URL (only on first display or retry).
        callback_url = self._get_callback_url()
        if not self._auth_state:
            self._auth_state = str(uuid.uuid4())
        auth_url = self._lwa_client.get_authorization_url(
            redirect_uri=callback_url,
            scope=SCOPE_SMAPI,
            state=self._auth_state,
        )

        return self.async_show_form(
            step_id="auth_smapi",
            data_schema=vol.Schema({}),
            description_placeholders={
                "auth_url": auth_url,
                "callback_url": callback_url,
            },
            errors=errors,
        )

    async def async_step_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Create SMAPI skill pointing at HA webhook."""
        if self._lwa_client is None:
            return await self.async_step_user()

        errors: dict[str, str] = {}

        # This step shows a progress form; actual work happens on submit.
        if user_input is not None:
            try:
                smapi = SMAPI(self._lwa_client)
                locales = self._user_input.get(CONF_LOCALES, _DEFAULT_LOCALES)
                invocation_name = self._user_input.get(
                    CONF_INVOCATION_NAME, DEFAULT_SKILL_NAME
                )

                # Build the webhook URL that Alexa will POST to.
                ha_url = self._user_input[CONF_HA_URL].rstrip("/")
                endpoint_uri = f"{ha_url}{_SKILL_WEBHOOK_PATH}"

                _LOGGER.info(
                    "Setup: Creating skill '%s' with endpoint %s",
                    invocation_name, endpoint_uri,
                )
                models = {loc: get_model(loc, invocation_name) for loc in locales}
                setup_result = await smapi.async_setup_skill_complete(
                    models=models,
                    skill_name=invocation_name,
                    endpoint_uri=endpoint_uri,
                )
                skill_id = setup_result["skill_id"]

                _LOGGER.info(
                    "Skill setup complete: skill_id=%s, endpoint=%s",
                    skill_id, endpoint_uri,
                )

                # Store results for the finish step.
                self._user_input[CONF_SKILL_ID] = skill_id
                self._user_input[CONF_VENDOR_ID] = setup_result.get("vendor_id", "")

                return await self.async_step_finish()

            except SMAPIError as err:
                _LOGGER.error("SMAPI setup failed: %s", err)
                errors["base"] = "smapi_error"
            except HomeAssistantError as err:
                _LOGGER.error("Setup failed: %s", err)
                errors["base"] = "unknown"
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="setup",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4: Confirm success and create the config entry."""
        if user_input is not None:
            # Create the config entry with all collected data.
            return self.async_create_entry(
                title="Alexa Actionable Notifications",
                data={
                    CONF_CLIENT_ID: self._user_input[CONF_CLIENT_ID],
                    CONF_CLIENT_SECRET: self._user_input[CONF_CLIENT_SECRET],
                    CONF_HA_URL: self._user_input[CONF_HA_URL],
                    CONF_HA_TOKEN: self._user_input[CONF_HA_TOKEN],
                    CONF_INVOCATION_NAME: self._user_input.get(
                        CONF_INVOCATION_NAME, DEFAULT_SKILL_NAME
                    ),
                    CONF_LOCALES: self._user_input.get(
                        CONF_LOCALES, _DEFAULT_LOCALES
                    ),
                    CONF_SKILL_ID: self._user_input.get(CONF_SKILL_ID, ""),
                    CONF_VENDOR_ID: self._user_input.get(CONF_VENDOR_ID, ""),
                    CONF_REFRESH_TOKEN: self._user_input.get(
                        CONF_REFRESH_TOKEN, ""
                    ),
                },
            )

        return self.async_show_form(
            step_id="finish",
            description_placeholders={
                "skill_id": self._user_input.get(CONF_SKILL_ID, ""),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _register_callback_view(self) -> None:
        """Register the OAuth callback view (idempotent)."""
        try:
            self.hass.http.register_view(AlexaAuthCallbackView(self.hass))
        except ValueError:
            # Already registered.
            pass

    def _get_callback_url(self) -> str:
        """Build the full OAuth callback URL.

        Prefers the URL the user explicitly entered in the form so that
        the OAuth redirect matches what was whitelisted in the LWA security
        profile (typically an external HTTPS URL).  Falls back to HA's
        auto-detected URL only when no manual URL was provided.
        """
        base_url = self._user_input.get(CONF_HA_URL, "")
        if not base_url:
            try:
                base_url = get_url(self.hass)
            except NoURLAvailableError:
                pass
        return f"{base_url}{_CALLBACK_PATH}"


class AlexaActionsOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Alexa Actions SMAPI."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                entry_data = self._config_entry.data

                current_ha_url = entry_data.get(CONF_HA_URL, "")
                new_ha_url = user_input.get(CONF_HA_URL, current_ha_url)

                current_invocation = entry_data.get(
                    CONF_INVOCATION_NAME, DEFAULT_SKILL_NAME
                )
                new_invocation = user_input.get(
                    CONF_INVOCATION_NAME, current_invocation
                )

                ha_url_changed = new_ha_url != current_ha_url
                invocation_changed = new_invocation != current_invocation

                skill_id = entry_data.get(CONF_SKILL_ID, "")

                # Update SMAPI if invocation or HA URL changed.
                if (ha_url_changed or invocation_changed) and skill_id:
                    lwa_client = LWAClient(
                        self.hass,
                        entry_data[CONF_CLIENT_ID],
                        entry_data[CONF_CLIENT_SECRET],
                    )
                    refresh_token = entry_data.get(CONF_REFRESH_TOKEN, "")
                    if refresh_token:
                        lwa_client.set_refresh_token(
                            SCOPE_SMAPI, refresh_token,
                        )

                    smapi = SMAPI(lwa_client)
                    try:
                        if ha_url_changed or invocation_changed:
                            # Update manifest with new endpoint/invocation.
                            ha_url = new_ha_url.rstrip("/")
                            endpoint_uri = f"{ha_url}{_SKILL_WEBHOOK_PATH}"
                            locales = self._config_entry.options.get(
                                CONF_LOCALES,
                                entry_data.get(CONF_LOCALES, _DEFAULT_LOCALES),
                            )
                            await smapi.async_update_manifest(
                                skill_id=skill_id,
                                skill_name=new_invocation,
                                locales=locales,
                                endpoint_uri=endpoint_uri,
                            )
                    finally:
                        await lwa_client.async_close()

                # Update the config entry data with new values.
                new_data = dict(entry_data)
                new_data[CONF_HA_URL] = new_ha_url
                new_data[CONF_INVOCATION_NAME] = new_invocation
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )

                return self.async_create_entry(
                    title="",
                    data={
                        CONF_INVOCATION_NAME: new_invocation,
                        CONF_HA_URL: new_ha_url,
                    },
                )

            except HomeAssistantError as err:
                _LOGGER.error("Options update failed: %s", err)
                errors["base"] = "smapi_error"
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("Unexpected error in options flow")
                errors["base"] = "unknown"

        current_invocation = self._config_entry.data.get(
            CONF_INVOCATION_NAME, DEFAULT_SKILL_NAME
        )
        current_ha_url = self._config_entry.data.get(CONF_HA_URL, "")

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_INVOCATION_NAME, default=current_invocation
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_HA_URL, default=current_ha_url
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
        )
