"""Constants for the Alexa Actions SMAPI integration."""

DOMAIN = "alexa_actions"

LWA_AUTH_URL = "https://www.amazon.com/ap/oa"
LWA_TOKEN_URL = "https://api.amazon.com/auth/O2/token"

SCOPE_SMAPI = "alexa::ask:skills:readwrite alexa::ask:models:readwrite"

SMAPI_BASE_URL = "https://api.amazonalexa.com"

DEFAULT_SKILL_NAME = "actionable notifications"

CONF_HA_URL = "ha_url"
CONF_HA_TOKEN = "ha_token"
CONF_INVOCATION_NAME = "invocation_name"
CONF_LOCALES = "locales"
CONF_SKILL_ID = "skill_id"
CONF_VENDOR_ID = "vendor_id"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_AWS_ACCESS_KEY_ID = "aws_access_key_id"
CONF_AWS_SECRET_ACCESS_KEY = "aws_secret_access_key"
CONF_AWS_REGION = "aws_region"

SERVICE_SEND = "send"
INPUT_TEXT_ENTITY = "input_text.alexa_actionable_notification"
EVENT_ALEXA_ACTIONABLE_NOTIFICATION = "alexa_actionable_notification"

# Response type constants (used by skill_handler and __init__)
RESPONSE_YES = "ResponseYes"
RESPONSE_NO = "ResponseNo"
RESPONSE_NONE = "ResponseNone"
RESPONSE_SELECT = "ResponseSelect"
RESPONSE_NUMERIC = "ResponseNumeric"
RESPONSE_DURATION = "ResponseDuration"
RESPONSE_STRING = "ResponseString"
RESPONSE_DATE_TIME = "ResponseDateTime"
