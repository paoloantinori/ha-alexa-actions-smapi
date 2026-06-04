"""Interaction models for Alexa actionable notifications."""


def get_model(locale: str, invocation_name: str) -> dict:
    """Return the interaction model for the given locale.

    TODO: ACT-8 will provide the full multi-locale models.
    """
    return {
        "interactionModel": {
            "languageModel": {
                "invocationName": invocation_name,
                "intents": [
                    {"name": "AMAZON.YesIntent", "samples": []},
                    {"name": "AMAZON.NoIntent", "samples": []},
                    {"name": "AMAZON.CancelIntent", "samples": []},
                    {"name": "AMAZON.StopIntent", "samples": []},
                    {"name": "AMAZON.FallbackIntent", "samples": []},
                ],
            }
        }
    }
