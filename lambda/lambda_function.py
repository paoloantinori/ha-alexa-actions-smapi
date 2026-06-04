"""Alexa Actions SMAPI - Lambda handler.

Processes Alexa actionable-notification events and forwards responses
to a Home Assistant instance via its REST API.

Configuration is read from environment variables:

  HOME_ASSISTANT_URL  – Base URL of the Home Assistant instance
  VERIFY_SSL          – "true" / "false" (default "true")
  TOKEN               – Long-lived access token; falls back to
                        account-linking token when empty
  DEBUG               – "true" / "false" (default "false")
"""

import json
import os
from typing import Optional, Union

import isodate
import urllib3
from ask_sdk_core.dispatch_components import AbstractExceptionHandler
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.dispatch_components import AbstractRequestInterceptor
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.utils import (
    get_account_linking_access_token,
    is_request_type,
    is_intent_name,
    get_intent_name,
    get_slot,
    get_slot_value,
)
from ask_sdk_model import SessionEndedReason
from ask_sdk_model.slu.entityresolution import StatusCode

import prompts
from const import (
    INPUT_TEXT_ENTITY,
    LOCALIZATION_ATTR,
    RESPONSE_DATE_TIME,
    RESPONSE_DURATION,
    RESPONSE_NO,
    RESPONSE_NONE,
    RESPONSE_NUMERIC,
    RESPONSE_SELECT,
    RESPONSE_STRING,
    RESPONSE_YES,
)
from schemas import HaState, HaStateError
from utils import get_logger, _string_to_bool

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
HOME_ASSISTANT_URL = os.environ.get("HOME_ASSISTANT_URL", "").rstrip("/")
VERIFY_SSL = _string_to_bool(os.environ.get("VERIFY_SSL", "true"), default=True)
CONFIGURED_TOKEN = os.environ.get("TOKEN", "")
DEBUG = _string_to_bool(os.environ.get("DEBUG", "false"), default=False)

logger = get_logger(DEBUG)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _handle_response(handler_input, speak_out):
    """Build a response, optionally speaking *speak_out*.

    When *speak_out* is falsy (empty string / None) the response has no
    speech output — used when ``suppress_confirmation`` is enabled.
    """
    if speak_out:
        return handler_input.response_builder.speak(speak_out).response
    return handler_input.response_builder.response


def _init_http_pool():
    """Create a urllib3 pool manager with TLS settings from config."""
    return urllib3.PoolManager(
        cert_reqs="CERT_REQUIRED" if VERIFY_SSL else "CERT_NONE",
        timeout=urllib3.Timeout(connect=10.0, read=10.0),
    )


# Module-level singleton — reused across warm Lambda invocations
_http_pool = _init_http_pool()

# Loaded once at first access, then cached for the lifetime of the container.
_cached_language_strings: Optional[dict] = None


# ---------------------------------------------------------------------------
# Home Assistant API client
# ---------------------------------------------------------------------------

class HomeAssistant:
    """Communicates with the Home Assistant REST API.

    Unlike the original implementation this is a plain class — each handler
    instantiates its own object.  State is stored on the instance, not shared
    via a monostate pattern.
    """

    def __init__(self, handler_input=None):
        self.ha_state: Optional[Union[HaState, HaStateError]] = None
        self.http = _http_pool
        self.handler_input = handler_input
        self.language_strings = {}
        self.token = ""

        if handler_input:
            self.language_strings = handler_input.attributes_manager.request_attributes.get(
                LOCALIZATION_ATTR, {}
            )
            self.token = self._fetch_token() if CONFIGURED_TOKEN == "" else CONFIGURED_TOKEN
            self.get_ha_state()

    # -- Token handling -----------------------------------------------------

    def _fetch_token(self):
        """Retrieve the account-linking access token from Alexa."""
        logger.debug("Fetching Home Assistant token from Alexa")
        return get_account_linking_access_token(self.handler_input)

    # -- Error helpers ------------------------------------------------------

    def _set_ha_error(self, prompt_key):
        """Set *ha_state* to an error using the localized prompt *prompt_key*."""
        self.ha_state = HaStateError(text=self.language_strings.get(prompt_key, prompt_key))

    # -- HTTP primitives ----------------------------------------------------

    @staticmethod
    def _build_url(*path):
        return f"{HOME_ASSISTANT_URL}/" + "/".join(path)

    def _get_headers(self, extra_headers=None):
        """Return the authorization headers, optionally merged with extras."""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _check_response_errors(self, response):
        """Return an error string if *response* indicates failure, else ``False``."""
        if response.status == 401:
            logger.error("401 Error from Home Assistant.")
            logger.debug(response.data)
            return "Error 401 " + self.language_strings.get(prompts.ERROR_401, "Unauthorized")
        if response.status == 404:
            logger.error("404 Error from Home Assistant.")
            logger.debug(response.data)
            return "Error 404 " + self.language_strings.get(prompts.ERROR_404, "Not Found")
        if response.status >= 400:
            logger.error("%s Error from Home Assistant.", response.status)
            logger.debug(response.data)
            return (
                f"Error {response.status}, "
                + self.language_strings.get(prompts.ERROR_400, "Bad Request")
            )
        return False

    def _request(self, method, *path, body=None, extra_headers=None):
        """Shared HTTP request with error handling for GET and POST."""
        headers = self._get_headers(extra_headers)
        url = self._build_url(*path)
        request_kwargs: dict = {"headers": headers}
        if body is not None:
            request_kwargs["body"] = json.dumps(body).encode("utf-8")
        response = self.http.request(method, url, **request_kwargs)
        logger.debug("Raw %s response: %s", method, response.data)
        errors = self._check_response_errors(response)
        if errors:
            self.ha_state = HaStateError(text=errors)
            return None
        return response

    def _get(self, *path, extra_headers=None):
        return self._request("GET", *path, extra_headers=extra_headers)

    def _post(self, *path, body, extra_headers=None):
        return self._request("POST", *path, body=body, extra_headers=extra_headers)

    # -- State decoding -----------------------------------------------------

    def _decode_response(self, response):
        decoded = json.loads(response.data.decode("utf-8")).get("state")
        logger.debug("Decoded response: %s", decoded)
        if decoded:
            return json.loads(decoded)
        logger.error("No entity state provided by Home Assistant.")
        self._set_ha_error(prompts.ERROR_CONFIG)
        return None

    def clear_state(self):
        """Reset the local HA state."""
        logger.debug("Clearing Home Assistant local state")
        self.ha_state = None

    # -- High-level operations -----------------------------------------------

    def get_ha_state(self):
        """Fetch the current state of the actionable-notification entity."""
        response = self._get("api", "states", INPUT_TEXT_ENTITY)
        if not response:
            return
        response = self._decode_response(response)
        if not response:
            return
        self.ha_state = HaState(
            event_id=response.get("event"),
            suppress_confirmation=_string_to_bool(response.get("suppress_confirmation")),
            text=response.get("text"),
        )

    def post_ha_event(self, response, response_type, **kwargs):
        """Post an event response back to Home Assistant.

        Returns:
            A string to speak, or an empty string when
            ``suppress_confirmation`` is enabled.
        """
        if not isinstance(self.ha_state, HaState):
            return ""

        body = {
            "event_id": self.ha_state.event_id,
            "event_response": response,
            "event_response_type": response_type,
        }
        body.update(kwargs)

        if self.handler_input:
            person = self.handler_input.request_envelope.context.system.person
            if person:
                body["event_person_id"] = person.person_id

        post_response = self._post("api", "events", "alexa_actionable_notification", body=body)
        if not post_response:
            return self.ha_state.text if self.ha_state else ""

        suppress = self.ha_state.suppress_confirmation
        self.clear_state()
        if not suppress:
            return self.language_strings.get(prompts.OKAY, "Okay")
        return ""

    def get_value_for_slot(self, slot_name):
        """Return the first successfully-resolved slot value name, or ``None``."""
        slot = get_slot(self.handler_input, slot_name=slot_name)
        if not slot or not slot.resolutions:
            return None
        if not slot.resolutions.resolutions_per_authority:
            return None
        for resolution in slot.resolutions.resolutions_per_authority:
            if resolution.status.code == StatusCode.ER_SUCCESS_MATCH:
                for value in resolution.values:
                    if value.value and value.value.name:
                        return value.value.name
        return None


# ---------------------------------------------------------------------------
# Alexa request handlers
# ---------------------------------------------------------------------------

class LaunchRequestHandler(AbstractRequestHandler):
    """Handle the initial skill launch — speak the notification text."""

    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        ha_obj = HomeAssistant(handler_input)
        if isinstance(ha_obj.ha_state, HaStateError):
            return handler_input.response_builder.speak(ha_obj.ha_state.text).response
        if not ha_obj.ha_state:
            return handler_input.response_builder.response
        speak_output = ha_obj.ha_state.text
        event_id = ha_obj.ha_state.event_id
        handler = handler_input.response_builder.speak(speak_output)
        if event_id:
            handler.ask("")
        return handler.response


class YesIntentHandler(AbstractRequestHandler):
    """Handle an affirmative user response."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.YesIntent")(handler_input)

    def handle(self, handler_input):
        logger.info("Yes Intent Handler triggered")
        ha_obj = HomeAssistant(handler_input)
        speak_output = ha_obj.post_ha_event(RESPONSE_YES, RESPONSE_YES)
        return _handle_response(handler_input, speak_output)


class NoIntentHandler(AbstractRequestHandler):
    """Handle a negative user response."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.NoIntent")(handler_input)

    def handle(self, handler_input):
        logger.info("No Intent Handler triggered")
        ha_obj = HomeAssistant(handler_input)
        speak_output = ha_obj.post_ha_event(RESPONSE_NO, RESPONSE_NO)
        return _handle_response(handler_input, speak_output)


class NumericIntentHandler(AbstractRequestHandler):
    """Handle a numeric slot value."""

    def can_handle(self, handler_input):
        return is_intent_name("Number")(handler_input)

    def handle(self, handler_input):
        logger.info("Numeric Intent Handler triggered")
        ha_obj = HomeAssistant(handler_input)
        number = get_slot_value(handler_input, "Numbers")
        logger.debug("Number: %s", number)
        if number == "?":
            raise ValueError("Numeric slot value could not be resolved")
        speak_output = ha_obj.post_ha_event(number, RESPONSE_NUMERIC)
        return _handle_response(handler_input, speak_output)


class StringIntentHandler(AbstractRequestHandler):
    """Handle a free-text string slot value."""

    def can_handle(self, handler_input):
        return is_intent_name("String")(handler_input)

    def handle(self, handler_input):
        logger.info("String Intent Handler triggered")
        ha_obj = HomeAssistant(handler_input)
        strings = get_slot_value(handler_input, "Strings")
        speak_output = ha_obj.post_ha_event(strings, RESPONSE_STRING)
        return _handle_response(handler_input, speak_output)


class SelectIntentHandler(AbstractRequestHandler):
    """Handle a predefined selection via slot resolution."""

    def can_handle(self, handler_input):
        return is_intent_name("Select")(handler_input)

    def handle(self, handler_input):
        logger.info("Selection Intent Handler triggered")
        ha_obj = HomeAssistant(handler_input)
        selection = ha_obj.get_value_for_slot("Selections")
        if not selection:
            raise ValueError("Selection slot value could not be resolved")
        ha_obj.post_ha_event(selection, RESPONSE_SELECT)
        data = handler_input.attributes_manager.request_attributes[LOCALIZATION_ATTR]
        speak_output = data[prompts.SELECTED].format(selection)
        return _handle_response(handler_input, speak_output)


class DurationIntentHandler(AbstractRequestHandler):
    """Handle an ISO 8601 duration slot, converting to total seconds."""

    def can_handle(self, handler_input):
        return is_intent_name("Duration")(handler_input)

    def handle(self, handler_input):
        logger.info("Duration Intent Handler triggered")
        ha_obj = HomeAssistant(handler_input)
        duration = get_slot_value(handler_input, "Durations")
        speak_output = ha_obj.post_ha_event(
            isodate.parse_duration(duration).total_seconds(),
            RESPONSE_DURATION,
        )
        return _handle_response(handler_input, speak_output)


class DateTimeIntentHandler(AbstractRequestHandler):
    """Handle date and time slot values, posting a JSON representation."""

    def can_handle(self, handler_input):
        return is_intent_name("Date")(handler_input)

    def handle(self, handler_input):
        logger.info("Date Intent Handler triggered")
        ha_obj = HomeAssistant(handler_input)
        date = get_slot_value(handler_input, "Dates")
        time = get_slot_value(handler_input, "Times")
        if not date and not time:
            raise ValueError("Both date and time slot values are empty")
        speak_output = ha_obj.post_ha_event(
            json.dumps({**self._parse_date(date), **self._parse_time(time)}),
            RESPONSE_DATE_TIME,
        )
        return _handle_response(handler_input, speak_output)

    @staticmethod
    def _parse_date(date):
        """Parse an Alexa date string ``YYYY-MM-DD`` into components."""
        date_data = {"day": None, "month": None, "year": None}
        if not date:
            return date_data
        parts = date.split("-")
        date_data["year"] = parts[0] if len(parts) >= 1 else None
        date_data["month"] = parts[1] if len(parts) >= 2 else None
        date_data["day"] = parts[2] if len(parts) >= 3 else None
        return date_data

    @staticmethod
    def _parse_time(time):
        """Parse an Alexa time string ``HH:MM`` or suffixed value into components."""
        time_data = {"seconds": None, "minute": None, "hour": None}
        if not time:
            return time_data
        lower = time.lower()
        if "s" in lower:
            time_data["seconds"] = lower.replace("s", "")
            return time_data
        if "m" in lower:
            time_data["minute"] = lower.replace("m", "")
            return time_data
        if "h" in lower:
            time_data["hour"] = lower.replace("h", "")
            return time_data
        parts = time.split(":")
        time_data["hour"] = parts[0] if len(parts) >= 1 else None
        time_data["minute"] = parts[1] if len(parts) >= 2 else None
        time_data["seconds"] = parts[2] if len(parts) >= 3 else None
        return time_data


class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Handle cancel and stop intents."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.CancelIntent")(handler_input) or is_intent_name(
            "AMAZON.StopIntent"
        )(handler_input)

    def handle(self, handler_input):
        data = handler_input.attributes_manager.request_attributes[LOCALIZATION_ATTR]
        speak_output = data[prompts.STOP_MESSAGE]
        return _handle_response(handler_input, speak_output)


class FallbackHandler(AbstractRequestHandler):
    """Handle unrecognized intents by posting RESPONSE_NONE."""

    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        ha_obj = HomeAssistant(handler_input)
        ha_obj.post_ha_event(RESPONSE_NONE, RESPONSE_NONE)
        return handler_input.response_builder.response


class SessionEndedRequestHandler(AbstractRequestHandler):
    """Handle session-ended events (timeout, user-initiated)."""

    def can_handle(self, handler_input):
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        ha_obj = HomeAssistant(handler_input)
        reason = handler_input.request_envelope.request.reason
        if reason in (
            SessionEndedReason.EXCEEDED_MAX_REPROMPTS,
            SessionEndedReason.USER_INITIATED,
        ):
            ha_obj.post_ha_event(RESPONSE_NONE, RESPONSE_NONE)
        return handler_input.response_builder.response


class IntentReflectorHandler(AbstractRequestHandler):
    """Reflect the triggered intent name back to the user (debug aid)."""

    def can_handle(self, handler_input):
        return is_request_type("IntentRequest")(handler_input)

    def handle(self, handler_input):
        intent_name = get_intent_name(handler_input)
        speak_output = "You just triggered " + intent_name + "."
        return handler_input.response_builder.speak(speak_output).response


# ---------------------------------------------------------------------------
# Exception handler
# ---------------------------------------------------------------------------

class CatchAllExceptionHandler(AbstractExceptionHandler):
    """Catch-all exception handler.

    When a ``handler_input`` is available this returns an acoustic-error
    message that repeats the current notification text.  Otherwise a generic
    configuration error is spoken.
    """

    def can_handle(self, _handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(exception, exc_info=True)
        data = handler_input.attributes_manager.request_attributes.get(LOCALIZATION_ATTR, {})
        try:
            ha_obj = HomeAssistant(handler_input)
            if ha_obj.ha_state and ha_obj.ha_state.text:
                speak_output = data.get(
                    prompts.ERROR_ACOUSTIC,
                    "There was an error with the acoustic request: {text}",
                ).format(ha_obj.ha_state.text)
                return handler_input.response_builder.speak(speak_output).ask("").response
        except Exception:  # noqa: BLE001 — defensive; never let error handling crash
            logger.warning("CatchAllExceptionHandler could not initialise HomeAssistant")
        speak_output = data.get(prompts.ERROR_CONFIG, "There was an error with the skill configuration.")
        return handler_input.response_builder.speak(speak_output).response


# ---------------------------------------------------------------------------
# Localization interceptor
# ---------------------------------------------------------------------------

def _load_language_strings() -> dict:
    """Load and cache ``language_strings.json`` (once per Lambda container)."""
    global _cached_language_strings
    if _cached_language_strings is None:
        with open("language_strings.json", encoding="utf-8") as fh:
            _cached_language_strings = json.load(fh)
    return _cached_language_strings  # type: ignore[return-value]


class LocalizationInterceptor(AbstractRequestInterceptor):
    """Load locale-specific strings from ``language_strings.json``."""

    def process(self, handler_input):
        locale = handler_input.request_envelope.request.locale
        language_data = _load_language_strings()
        data = language_data.get(locale[:2], {})
        if locale in language_data:
            data.update(language_data[locale])
        handler_input.attributes_manager.request_attributes[LOCALIZATION_ATTR] = data


# ---------------------------------------------------------------------------
# Skill builder — register all handlers
# ---------------------------------------------------------------------------

sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(YesIntentHandler())
sb.add_request_handler(NoIntentHandler())
sb.add_request_handler(NumericIntentHandler())
sb.add_request_handler(StringIntentHandler())
sb.add_request_handler(SelectIntentHandler())
sb.add_request_handler(DurationIntentHandler())
sb.add_request_handler(DateTimeIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(IntentReflectorHandler())
sb.add_exception_handler(CatchAllExceptionHandler())
sb.add_global_request_interceptor(LocalizationInterceptor())

lambda_handler = sb.lambda_handler()
