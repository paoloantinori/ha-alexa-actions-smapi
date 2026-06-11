"""Tests for custom_components/alexa_actions/models.py — interaction model generation."""

import sys
import os

# Ensure custom_components is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.alexa_actions.models import (
    get_model,
    get_default_invocation,
    LOCALE_LABELS,
    _LOCALE_DATA,
    _BUILTIN_INTENTS,
    _build_intents,
)


REQUIRED_BUILTIN_INTENTS = [
    "AMAZON.YesIntent",
    "AMAZON.NoIntent",
    "AMAZON.HelpIntent",
    "AMAZON.CancelIntent",
    "AMAZON.StopIntent",
    "AMAZON.NavigateHomeIntent",
    "AMAZON.FallbackIntent",
]

CUSTOM_INTENTS = ["String", "Select", "Number", "Duration", "Date", "FreeForm"]


class TestLocaleLabels:
    """Tests for LOCALE_LABELS mapping."""

    def test_key_locales_present(self):
        for locale in ["en-US", "en-GB", "de-DE", "fr-FR", "fr-CA", "it-IT", "pt-BR", "es-ES"]:
            assert locale in LOCALE_LABELS, f"{locale} missing from LOCALE_LABELS"

    def test_minimum_locale_count(self):
        assert len(LOCALE_LABELS) >= 8

    def test_labels_are_non_empty_strings(self):
        for locale, label in LOCALE_LABELS.items():
            assert isinstance(label, str) and len(label) > 0, (
                f"Locale {locale} has empty/invalid label"
            )


class TestGetDefaultInvocation:
    """Tests for get_default_invocation()."""

    def test_us_english_invocation(self):
        assert get_default_invocation("en-US") == "actionable notifications"

    def test_german_invocation(self):
        assert get_default_invocation("de-DE") == "aktionstasten benachrichtigungen"

    def test_unknown_locale_falls_back_to_us(self):
        result = get_default_invocation("xx-XX")
        assert result == "actionable notifications"

    def test_returns_non_empty_string(self):
        for locale in _LOCALE_DATA:
            inv = get_default_invocation(locale)
            assert isinstance(inv, str) and len(inv) > 0


class TestGetModel:
    """Tests for get_model() — full interaction model generation."""

    def test_model_structure(self):
        model = get_model("en-US", "test skill")
        assert "interactionModel" in model
        lang_model = model["interactionModel"]["languageModel"]
        assert "invocationName" in lang_model
        assert "intents" in lang_model

    def test_invocation_name(self):
        model = get_model("en-US", "my skill")
        assert model["interactionModel"]["languageModel"]["invocationName"] == "my skill"

    def test_empty_invocation_uses_locale_default(self):
        model = get_model("en-US", "")
        # When invocation_name is falsy, it falls back to locale data
        inv = model["interactionModel"]["languageModel"]["invocationName"]
        assert inv == "actionable notifications"

    def test_all_builtin_intents_present(self):
        for locale in _LOCALE_DATA:
            model = get_model(locale, "test")
            intent_names = [
                i["name"]
                for i in model["interactionModel"]["languageModel"]["intents"]
            ]
            for intent in REQUIRED_BUILTIN_INTENTS:
                assert intent in intent_names, f"Missing {intent} in {locale}"

    def test_all_custom_intents_present(self):
        for locale in _LOCALE_DATA:
            model = get_model(locale, "test")
            intent_names = [
                i["name"]
                for i in model["interactionModel"]["languageModel"]["intents"]
            ]
            for intent in CUSTOM_INTENTS:
                assert intent in intent_names, f"Missing {intent} in {locale}"

    def test_string_intent_has_search_query_slot(self):
        model = get_model("en-US", "test")
        intents = {
            i["name"]: i
            for i in model["interactionModel"]["languageModel"]["intents"]
        }
        assert "slots" in intents["String"]
        assert intents["String"]["slots"][0]["type"] == "AMAZON.Person"
        assert intents["String"]["slots"][0]["name"] == "Strings"

    def test_select_intent_has_selections_slot(self):
        model = get_model("en-US", "test")
        intents = {
            i["name"]: i
            for i in model["interactionModel"]["languageModel"]["intents"]
        }
        assert intents["Select"]["slots"][0]["type"] == "Selections"

    def test_select_slot_type_has_values(self):
        model = get_model("en-US", "test")
        types = model["interactionModel"]["languageModel"]["types"]
        selections = next(t for t in types if t["name"] == "Selections")
        assert len(selections["values"]) > 0

    def test_number_intent_has_number_slot(self):
        model = get_model("en-US", "test")
        intents = {
            i["name"]: i
            for i in model["interactionModel"]["languageModel"]["intents"]
        }
        assert intents["Number"]["slots"][0]["type"] == "AMAZON.NUMBER"

    def test_duration_intent_has_duration_slot(self):
        model = get_model("en-US", "test")
        intents = {
            i["name"]: i
            for i in model["interactionModel"]["languageModel"]["intents"]
        }
        assert intents["Duration"]["slots"][0]["type"] == "AMAZON.DURATION"

    def test_date_intent_has_date_and_time_slots(self):
        model = get_model("en-US", "test")
        intents = {
            i["name"]: i
            for i in model["interactionModel"]["languageModel"]["intents"]
        }
        date_slots = {s["name"]: s["type"] for s in intents["Date"]["slots"]}
        assert date_slots["Dates"] == "AMAZON.DATE"
        assert date_slots["Times"] == "AMAZON.TIME"

    def test_builtin_intents_have_empty_samples(self):
        model = get_model("en-US", "test")
        intents = {
            i["name"]: i
            for i in model["interactionModel"]["languageModel"]["intents"]
        }
        for intent_name in REQUIRED_BUILTIN_INTENTS:
            assert intents[intent_name]["samples"] == []

    def test_custom_intents_have_samples(self):
        model = get_model("en-US", "test")
        intents = {
            i["name"]: i
            for i in model["interactionModel"]["languageModel"]["intents"]
        }
        for intent_name in CUSTOM_INTENTS:
            assert len(intents[intent_name]["samples"]) > 0, (
                f"{intent_name} has no samples"
            )

    def test_unknown_locale_falls_back(self):
        model = get_model("xx-XX", "test")
        assert "interactionModel" in model
        # Falls back to en-US invocation
        inv = model["interactionModel"]["languageModel"]["invocationName"]
        assert inv == "test"

    def test_total_intent_count(self):
        model = get_model("en-US", "test")
        intents = model["interactionModel"]["languageModel"]["intents"]
        expected_count = len(REQUIRED_BUILTIN_INTENTS) + len(CUSTOM_INTENTS)
        assert len(intents) == expected_count


class TestBuildIntents:
    """Tests for the _build_intents internal function."""

    def test_returns_list(self):
        locale_data = _LOCALE_DATA["en-US"]
        intents = _build_intents(locale_data)
        assert isinstance(intents, list)

    def test_each_intent_has_name(self):
        locale_data = _LOCALE_DATA["de-DE"]
        intents = _build_intents(locale_data)
        for intent in intents:
            assert "name" in intent

    def test_german_samples_are_german(self):
        locale_data = _LOCALE_DATA["de-DE"]
        intents = _build_intents(locale_data)
        string_intent = next(i for i in intents if i["name"] == "String")
        # German sample should contain German words
        assert any("meine" in s for s in string_intent["samples"])
