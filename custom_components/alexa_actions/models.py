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

# Per-locale data: invocation name + intent samples
_LOCALE_DATA: dict[str, dict[str, str | list[str]]] = {
    "en-US": {
        "invocation": "actionable notifications",
        "string_samples": [
            "{utterance}",
            "my answer is {utterance}",
            "I want to say {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "I choose {selection}",
            "select {selection}",
        ],
        "number_samples": [
            "{number}",
            "the number {number}",
            "my answer is {number}",
        ],
        "duration_samples": [
            "{duration}",
            "about {duration}",
            "wait {duration}",
        ],
        "date_samples": [
            "{date} at {time}",
            "{date}",
            "on {date} at {time}",
            "{time}",
        ],
    },
    "en-GB": {
        "invocation": "actionable notifications",
        "string_samples": [
            "{utterance}",
            "my answer is {utterance}",
            "I want to say {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "I choose {selection}",
            "select {selection}",
        ],
        "number_samples": [
            "{number}",
            "the number {number}",
            "my answer is {number}",
        ],
        "duration_samples": [
            "{duration}",
            "about {duration}",
            "wait {duration}",
        ],
        "date_samples": [
            "{date} at {time}",
            "{date}",
            "on {date} at {time}",
            "{time}",
        ],
    },
    "en-AU": {
        "invocation": "actionable notifications",
        "string_samples": [
            "{utterance}",
            "my answer is {utterance}",
            "I want to say {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "I choose {selection}",
            "select {selection}",
        ],
        "number_samples": [
            "{number}",
            "the number {number}",
            "my answer is {number}",
        ],
        "duration_samples": [
            "{duration}",
            "about {duration}",
            "wait {duration}",
        ],
        "date_samples": [
            "{date} at {time}",
            "{date}",
            "on {date} at {time}",
            "{time}",
        ],
    },
    "en-CA": {
        "invocation": "actionable notifications",
        "string_samples": [
            "{utterance}",
            "my answer is {utterance}",
            "I want to say {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "I choose {selection}",
            "select {selection}",
        ],
        "number_samples": [
            "{number}",
            "the number {number}",
            "my answer is {number}",
        ],
        "duration_samples": [
            "{duration}",
            "about {duration}",
            "wait {duration}",
        ],
        "date_samples": [
            "{date} at {time}",
            "{date}",
            "on {date} at {time}",
            "{time}",
        ],
    },
    "de-DE": {
        "invocation": "aktionstasten benachrichtigungen",
        "string_samples": [
            "{utterance}",
            "meine Antwort ist {utterance}",
            "ich möchte sagen {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "ich wähle {selection}",
            "Option {selection}",
        ],
        "number_samples": [
            "{number}",
            "die Nummer {number}",
            "meine Antwort ist {number}",
        ],
        "duration_samples": [
            "{duration}",
            "ungefähr {duration}",
            "warte {duration}",
        ],
        "date_samples": [
            "{date} um {time}",
            "{date}",
            "am {date} um {time}",
        ],
    },
    "fr-FR": {
        "invocation": "notifications actionnables",
        "string_samples": [
            "{utterance}",
            "ma réponse est {utterance}",
            "je veux dire {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "je choisis {selection}",
            "option {selection}",
        ],
        "number_samples": [
            "{number}",
            "le nombre {number}",
            "ma réponse est {number}",
        ],
        "duration_samples": [
            "{duration}",
            "environ {duration}",
            "attendre {duration}",
        ],
        "date_samples": [
            "{date} à {time}",
            "{date}",
            "le {date} à {time}",
        ],
    },
    "fr-CA": {
        "invocation": "notifications actionnables",
        "string_samples": [
            "{utterance}",
            "ma réponse est {utterance}",
            "je veux dire {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "je choisis {selection}",
            "option {selection}",
        ],
        "number_samples": [
            "{number}",
            "le nombre {number}",
            "ma réponse est {number}",
        ],
        "duration_samples": [
            "{duration}",
            "environ {duration}",
            "attendre {duration}",
        ],
        "date_samples": [
            "{date} à {time}",
            "{date}",
            "le {date} à {time}",
        ],
    },
    "it-IT": {
        "invocation": "notifiche azionabili",
        "string_samples": [
            "{utterance}",
            "la mia risposta è {utterance}",
            "voglio dire {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "scelgo {selection}",
            "opzione {selection}",
        ],
        "number_samples": [
            "{number}",
            "il numero {number}",
            "la mia risposta è {number}",
        ],
        "duration_samples": [
            "{duration}",
            "circa {duration}",
            "aspetta {duration}",
        ],
        "date_samples": [
            "{date} alle {time}",
            "{date}",
            "il {date} alle {time}",
        ],
    },
    "pt-BR": {
        "invocation": "notificações acionáveis",
        "string_samples": [
            "{utterance}",
            "minha resposta é {utterance}",
            "eu quero dizer {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "eu escolho {selection}",
            "opção {selection}",
        ],
        "number_samples": [
            "{number}",
            "o número {number}",
            "minha resposta é {number}",
        ],
        "duration_samples": [
            "{duration}",
            "cerca de {duration}",
            "esperar {duration}",
        ],
        "date_samples": [
            "{date} às {time}",
            "{date}",
            "em {date} às {time}",
        ],
    },
    "es-ES": {
        "invocation": "notificaciones procesables",
        "string_samples": [
            "{utterance}",
            "mi respuesta es {utterance}",
            "quiero decir {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "elijo {selection}",
            "opción {selection}",
        ],
        "number_samples": [
            "{number}",
            "el número {number}",
            "mi respuesta es {number}",
        ],
        "duration_samples": [
            "{duration}",
            "aproximadamente {duration}",
            "espera {duration}",
        ],
        "date_samples": [
            "{date} a las {time}",
            "{date}",
            "el {date} a las {time}",
        ],
    },
    "es-MX": {
        "invocation": "notificaciones procesables",
        "string_samples": [
            "{utterance}",
            "mi respuesta es {utterance}",
            "quiero decir {utterance}",
        ],
        "select_samples": [
            "{selection}",
            "elijo {selection}",
            "opción {selection}",
        ],
        "number_samples": [
            "{number}",
            "el número {number}",
            "mi respuesta es {number}",
        ],
        "duration_samples": [
            "{duration}",
            "aproximadamente {duration}",
            "espera {duration}",
        ],
        "date_samples": [
            "{date} a las {time}",
            "{date}",
            "el {date} a las {time}",
        ],
    },
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
            }
        }
    }


def _build_intents(locale_data: dict[str, str | list[str]]) -> list[dict]:
    """Build the full intent list for an interaction model."""
    intents: list[dict] = []

    # Built-in intents (no samples needed)
    for name in _BUILTIN_INTENTS:
        intents.append({"name": name, "samples": []})

    # String intent with AMAZON.SearchQuery slot
    intents.append(
        {
            "name": "String",
            "slots": [{"name": "Strings", "type": "AMAZON.SearchQuery"}],
            "samples": locale_data.get("string_samples", []),
        }
    )

    # Select intent with AMAZON.SearchQuery slot
    intents.append(
        {
            "name": "Select",
            "slots": [{"name": "Selections", "type": "AMAZON.SearchQuery"}],
            "samples": locale_data.get("select_samples", []),
        }
    )

    # Number intent with AMAZON.Number slot
    intents.append(
        {
            "name": "Number",
            "slots": [{"name": "Numbers", "type": "AMAZON.Number"}],
            "samples": locale_data.get("number_samples", []),
        }
    )

    # Duration intent with AMAZON.Duration slot
    intents.append(
        {
            "name": "Duration",
            "slots": [{"name": "Durations", "type": "AMAZON.Duration"}],
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
