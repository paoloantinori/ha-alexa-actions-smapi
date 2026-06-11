"""Interaction models for Alexa actionable notifications."""
from __future__ import annotations

# Human-readable labels for locale selection in config flow
LOCALE_LABELS: dict[str, str] = {
    "de-DE": "Deutsch (Deutschland)",
    "en-AU": "English (Australia)",
    "en-CA": "English (Canada)",
    "en-GB": "English (UK)",
    "en-US": "English (US)",
    "es-ES": "Español (España)",
    "es-MX": "Español (México)",
    "fr-CA": "Français (Canada)",
    "fr-FR": "Français (France)",
    "it-IT": "Italiano (Italia)",
    "pt-BR": "Português (Brasil)",
}

# ---------------------------------------------------------------------------
# Shared locale defaults — language families with identical samples
# ---------------------------------------------------------------------------

_EN_DEFAULT: dict[str, str | list[str]] = {
    "invocation": "actionable notifications",
    "string_samples": [
        "my answer is {Strings}",
        "I want to say {Strings}",
        "I say {Strings}",
    ],
    "select_samples": [
        "I choose {Selections}",
        "select {Selections}",
        "my choice is {Selections}",
    ],
    "number_samples": [
        "{Numbers}",
        "the number {Numbers}",
        "my answer is {Numbers}",
    ],
    "duration_samples": [
        "{Durations}",
        "about {Durations}",
        "wait {Durations}",
    ],
    "date_samples": [
        "{Dates} at {Times}",
        "{Dates}",
        "on {Dates} at {Times}",
        "{Times}",
    ],
}

_DE_DEFAULT: dict[str, str | list[str]] = {
    "invocation": "aktionstasten benachrichtigungen",
    "string_samples": [
        "meine Antwort ist {Strings}",
        "ich möchte sagen {Strings}",
        "ich sage {Strings}",
    ],
    "select_samples": [
        "ich wähle {Selections}",
        "Option {Selections}",
        "meine Wahl ist {Selections}",
    ],
    "number_samples": [
        "{Numbers}",
        "die Nummer {Numbers}",
        "meine Antwort ist {Numbers}",
    ],
    "duration_samples": [
        "{Durations}",
        "ungefähr {Durations}",
        "warte {Durations}",
    ],
    "date_samples": [
        "{Dates} um {Times}",
        "{Dates}",
        "am {Dates} um {Times}",
    ],
}

_FR_DEFAULT: dict[str, str | list[str]] = {
    "invocation": "notifications actionnables",
    "string_samples": [
        "ma réponse est {Strings}",
        "je veux dire {Strings}",
        "je dis {Strings}",
    ],
    "select_samples": [
        "je choisis {Selections}",
        "option {Selections}",
        "mon choix est {Selections}",
    ],
    "number_samples": [
        "{Numbers}",
        "le nombre {Numbers}",
        "ma réponse est {Numbers}",
    ],
    "duration_samples": [
        "{Durations}",
        "environ {Durations}",
        "attendre {Durations}",
    ],
    "date_samples": [
        "{Dates} à {Times}",
        "{Dates}",
        "le {Dates} à {Times}",
    ],
}

_IT_DEFAULT: dict[str, str | list[str]] = {
    "invocation": "notifiche azionabili",
    "string_samples": [
        "la mia risposta è {Strings}",
        "voglio dire {Strings}",
        "dico {Strings}",
    ],
    "select_samples": [
        "scelgo {Selections}",
        "opzione {Selections}",
        "la mia scelta è {Selections}",
    ],
    "number_samples": [
        "{Numbers}",
        "il numero {Numbers}",
        "la mia risposta è {Numbers}",
    ],
    "duration_samples": [
        "{Durations}",
        "circa {Durations}",
        "aspetta {Durations}",
    ],
    "date_samples": [
        "{Dates} alle {Times}",
        "{Dates}",
        "il {Dates} alle {Times}",
    ],
}

_PT_DEFAULT: dict[str, str | list[str]] = {
    "invocation": "notificações acionáveis",
    "string_samples": [
        "minha resposta é {Strings}",
        "eu quero dizer {Strings}",
        "eu digo {Strings}",
    ],
    "select_samples": [
        "eu escolho {Selections}",
        "opção {Selections}",
        "minha escolha é {Selections}",
    ],
    "number_samples": [
        "{Numbers}",
        "o número {Numbers}",
        "minha resposta é {Numbers}",
    ],
    "duration_samples": [
        "{Durations}",
        "cerca de {Durations}",
        "esperar {Durations}",
    ],
    "date_samples": [
        "{Dates} às {Times}",
        "{Dates}",
        "em {Dates} às {Times}",
    ],
}

_ES_DEFAULT: dict[str, str | list[str]] = {
    "invocation": "notificaciones procesables",
    "string_samples": [
        "mi respuesta es {Strings}",
        "quiero decir {Strings}",
        "digo {Strings}",
    ],
    "select_samples": [
        "elijo {Selections}",
        "opción {Selections}",
        "mi elección es {Selections}",
    ],
    "number_samples": [
        "{Numbers}",
        "el número {Numbers}",
        "mi respuesta es {Numbers}",
    ],
    "duration_samples": [
        "{Durations}",
        "aproximadamente {Durations}",
        "espera {Durations}",
    ],
    "date_samples": [
        "{Dates} a las {Times}",
        "{Dates}",
        "el {Dates} a las {Times}",
    ],
}

# Per-locale data: each locale references its language-family default
_LOCALE_DATA: dict[str, dict[str, str | list[str]]] = {
    "en-US": _EN_DEFAULT,
    "en-GB": _EN_DEFAULT,
    "en-AU": _EN_DEFAULT,
    "en-CA": _EN_DEFAULT,
    "de-DE": _DE_DEFAULT,
    "fr-FR": _FR_DEFAULT,
    "fr-CA": _FR_DEFAULT,
    "it-IT": _IT_DEFAULT,
    "pt-BR": _PT_DEFAULT,
    "es-ES": _ES_DEFAULT,
    "es-MX": _ES_DEFAULT,
}

# Built-in intents that require no custom samples
_BUILTIN_INTENTS: list[str] = [
    "AMAZON.YesIntent",
    "AMAZON.NoIntent",
    "AMAZON.HelpIntent",
    "AMAZON.CancelIntent",
    "AMAZON.StopIntent",
    "AMAZON.NavigateHomeIntent",
    "AMAZON.FallbackIntent",
]


def get_default_invocation(locale: str) -> str:
    """Return the default invocation name for a locale."""
    locale_data = _LOCALE_DATA.get(locale, _LOCALE_DATA["en-US"])
    return str(locale_data["invocation"])


def get_model(locale: str, invocation_name: str) -> dict:
    """Return the interaction model for the given locale.

    Falls back to en-US when the requested locale is not defined.
    """
    locale_data = _LOCALE_DATA.get(locale, _LOCALE_DATA["en-US"])
    invocation = invocation_name or str(locale_data["invocation"])

    return {
        "interactionModel": {
            "languageModel": {
                "invocationName": invocation,
                "intents": _build_intents(locale_data),
                "types": [
                    {
                        "name": "Selections",
                        "values": [
                            {"name": {"value": "Option One"}},
                            {"name": {"value": "Option Two"}},
                            {"name": {"value": "Option Three"}},
                            {"name": {"value": "Yes"}},
                            {"name": {"value": "No"}},
                        ],
                    }
                ],
            }
        }
    }


def get_model_with_options(
    locale: str, invocation_name: str, options: list[str],
) -> dict:
    """Return an interaction model with custom Selections slot values.

    Replaces the hardcoded slot values (Option One/Two/Three, Yes, No) with
    the caller-supplied *options* list.  Used for per-notification dynamic
    slot updates pushed to SMAPI before invoking the skill.
    """
    model = get_model(locale, invocation_name)
    model["interactionModel"]["languageModel"]["types"] = [
        {
            "name": "Selections",
            "values": [{"name": {"value": opt}} for opt in options],
        }
    ]
    return model


def _build_intents(locale_data: dict[str, str | list[str]]) -> list[dict]:
    """Build the full intent list for an interaction model."""
    intents: list[dict] = []

    # Built-in intents (no samples needed)
    for name in _BUILTIN_INTENTS:
        intents.append({"name": name, "samples": []})

    # String intent with AMAZON.Person slot (matches reference implementation)
    intents.append(
        {
            "name": "String",
            "slots": [{"name": "Strings", "type": "AMAZON.Person"}],
            "samples": locale_data.get("string_samples", []),
        }
    )

    # Select intent with custom Selections slot type (values defined in types array)
    intents.append(
        {
            "name": "Select",
            "slots": [{"name": "Selections", "type": "Selections"}],
            "samples": locale_data.get("select_samples", []),
        }
    )

    # Number intent with AMAZON.NUMBER slot (must be ALL CAPS for SMAPI)
    intents.append(
        {
            "name": "Number",
            "slots": [{"name": "Numbers", "type": "AMAZON.NUMBER"}],
            "samples": locale_data.get("number_samples", []),
        }
    )

    # Duration intent with AMAZON.DURATION slot (must be ALL CAPS for SMAPI)
    intents.append(
        {
            "name": "Duration",
            "slots": [{"name": "Durations", "type": "AMAZON.DURATION"}],
            "samples": locale_data.get("duration_samples", []),
        }
    )

    # Date intent with DATE and TIME slots
    intents.append(
        {
            "name": "Date",
            "slots": [
                {"name": "Dates", "type": "AMAZON.DATE"},
                {"name": "Times", "type": "AMAZON.TIME"},
            ],
            "samples": locale_data.get("date_samples", []),
        }
    )

    return intents
