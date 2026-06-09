#!/usr/bin/env python3
"""Validate the interaction model against SMAPI requirements.

Checks that would have caught the previous bugs:
  - Built-in slot types must be ALL CAPS (AMAZON.NUMBER, not AMAZON.Number)
  - Custom slot types must have values in the types array
  - Slot names in samples must match slot definitions
  - Model structure is valid

Optionally: test against a real SMAPI skill by providing LWA credentials
and a skill_id (refreshes the token and uploads the model, then polls build).
"""

import argparse
import json
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import models directly to avoid HA dependency from __init__.py
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "models",
    os.path.join(os.path.dirname(__file__), "..",
                 "custom_components", "alexa_actions", "models.py"),
)
_models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_models)
get_model = _models.get_model
LOCALE_LABELS = _models.LOCALE_LABELS

# Built-in slot types that SMAPI recognizes — must be ALL CAPS after "AMAZON."
BUILTIN_SLOT_TYPES = {
    "AMAZON.NUMBER", "AMAZON.DURATION", "AMAZON.DATE", "AMAZON.TIME",
    "AMAZON.SearchQuery", "AMAZON.Person", "AMAZON.City", "AMAZON.Country",
    "AMAZON.FirstName", "AMAZON.LastName", "AMAZON.Language", "AMAZON.Ordinal",
    "AMAZON.PhoneNumber", "AMAZON.PostalCode", "AMAZON.State", "AMAZON.StreetName",
    "AMAZON.Airline", "AMAZON.AirportCode", "AMAZON.Food",
    "AMAZON.Activity", "AMAZON.Animal", "AMAZON.Movie", "AMAZON.MusicRecording",
    "AMAZON.MusicGroup", "AMAZON.Book", "AMAZON.Game", "AMAZON.VideoGame",
    "AMAZON.SportsTeam", "AMAZON.Athlete", "AMAZON.Actor", "AMAZON.Director",
    "AMAZON.ScreeningEvent",
}


def validate_model(locale: str, invocation: str = "test skill") -> list[str]:
    """Validate a single locale's model. Returns list of errors (empty = valid)."""
    errors = []
    model = get_model(locale, invocation)

    # 1. Check top-level structure
    im = model.get("interactionModel", {})
    lm = im.get("languageModel", {})
    if not lm:
        errors.append("Missing interactionModel.languageModel")
        return errors

    intents = lm.get("intents", [])
    types = lm.get("types", [])

    # 2. Check each intent's slots
    for intent in intents:
        intent_name = intent.get("name", "<unnamed>")
        slots = intent.get("slots", [])
        samples = intent.get("samples", [])

        for slot in slots:
            slot_name = slot.get("name", "")
            slot_type = slot.get("type", "")

            if not slot_name or not slot_type:
                errors.append(f"Intent '{intent_name}': slot missing name or type")
                continue

            # 2a. Built-in types must use correct casing
            if slot_type.startswith("AMAZON."):
                suffix = slot_type.split(".", 1)[1]
                if suffix.upper() != suffix and slot_type not in BUILTIN_SLOT_TYPES:
                    errors.append(
                        f"Intent '{intent_name}', slot '{slot_name}': "
                        f"built-in type '{slot_type}' should be ALL CAPS "
                        f"(e.g. 'AMAZON.{suffix.upper()}')"
                    )
                if slot_type not in BUILTIN_SLOT_TYPES:
                    errors.append(
                        f"Intent '{intent_name}', slot '{slot_name}': "
                        f"unknown built-in type '{slot_type}'"
                    )
            else:
                # 2b. Custom types must have values in types array
                type_def = next((t for t in types if t["name"] == slot_type), None)
                if not type_def:
                    errors.append(
                        f"Intent '{intent_name}', slot '{slot_name}': "
                        f"custom type '{slot_type}' not defined in types array"
                    )
                elif not type_def.get("values"):
                    errors.append(
                        f"Intent '{intent_name}', slot '{slot_name}': "
                        f"custom type '{slot_type}' has no values"
                    )

            # 2c. Slot name must be referenced in at least one sample
            slot_ref = "{" + slot_name + "}"
            if samples and not any(slot_ref in s for s in samples):
                errors.append(
                    f"Intent '{intent_name}', slot '{slot_name}': "
                    f"slot is never referenced in samples (expected '{slot_ref}')"
                )

        # 2d. Sample utterances must only reference defined slots
        defined_names = {s["name"] for s in slots}
        for sample in samples:
            refs = re.findall(r'\{(\w+)\}', sample)
            for ref in refs:
                if ref not in defined_names:
                    errors.append(
                        f"Intent '{intent_name}': sample '{sample}' "
                        f"references '{{ {ref} }}' but no slot with that name exists"
                    )

    return errors


async def test_smapi_build(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    skill_id: str,
    locale: str = "it-IT",
) -> bool:
    """Upload model to a real SMAPI skill and poll build status."""
    import aiohttp
    import asyncio

    # 1. Refresh the access token
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            "https://api.amazon.com/auth/o2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        token_data = await resp.json()
        if "access_token" not in token_data:
            print(f"  ✗ Token refresh failed: {token_data}")
            return False
        access_token = token_data["access_token"]
        print(f"  ✓ Access token refreshed")

        headers = {"Authorization": f"Bearer {access_token}"}

        # 2. Upload the interaction model
        model = get_model(locale, "actionable notifications")
        url = (
            f"https://api.eu.amazonalexa.com/v1/skills/{skill_id}"
            f"/stages/development/interactionModel/locales/{locale}"
        )
        resp = await session.put(url, json=model, headers=headers)
        if resp.status not in (200, 204):
            body = await resp.text()
            print(f"  ✗ Model upload failed ({resp.status}): {body[:300]}")
            return False
        print(f"  ✓ Model uploaded for {locale}")

        # 3. Poll build status
        status_url = (
            f"https://api.eu.amazonalexa.com/v1/skills/{skill_id}"
            f"/status?resource=interactionModel"
        )
        for attempt in range(36):  # 3 minutes max
            await asyncio.sleep(5)
            resp = await session.get(status_url, headers=headers)
            data = await resp.json()
            im_data = data.get("interactionModel", {})
            locale_statuses = im_data.get("locales", im_data)
            locale_info = locale_statuses.get(locale, {})
            last_update = locale_info.get("lastUpdateRequest", {})
            status = last_update.get("status", "UNKNOWN")
            build_errors = last_update.get("errors", [])
            print(f"  [{attempt+1}] Build status: {status}")
            if status == "SUCCEEDED":
                print(f"  ✓ Model build SUCCEEDED for {locale}")
                return True
            if status == "FAILED":
                for err in build_errors:
                    print(f"  ✗ Build error: {err.get('code')}: {err.get('message')}")
                return False
        print(f"  ✗ Build timed out after 3 minutes")
        return False


def main():
    parser = argparse.ArgumentParser(description="Validate interaction models")
    parser.add_argument(
        "--locale", default=None,
        help="Validate single locale (default: all)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Dump the model JSON for inspection"
    )
    parser.add_argument(
        "--smapi", action="store_true",
        help="Test against real SMAPI (requires --client-id, --client-secret, "
             "--refresh-token, --skill-id)"
    )
    parser.add_argument("--client-id", default=os.environ.get("LWA_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("LWA_CLIENT_SECRET"))
    parser.add_argument("--refresh-token", default=os.environ.get("LWA_REFRESH_TOKEN"))
    parser.add_argument("--skill-id", default=os.environ.get("SMAPI_SKILL_ID"))
    args = parser.parse_args()

    locales = [args.locale] if args.locale else list(LOCALE_LABELS.keys())
    all_errors = []

    for locale in locales:
        if args.json:
            model = get_model(locale, "actionable notifications")
            print(f"\n{'='*60}")
            print(f"Model for {locale}:")
            print(json.dumps(model, indent=2))
            continue

        errors = validate_model(locale)
        if errors:
            print(f"✗ {locale}: {len(errors)} error(s)")
            for e in errors:
                print(f"  - {e}")
            all_errors.extend(errors)
        else:
            print(f"✓ {locale}: valid")

    if args.json:
        return

    if all_errors:
        print(f"\n✗ {len(all_errors)} total error(s) found")
        sys.exit(1)

    print(f"\n✓ All {len(locales)} locale(s) passed local validation")

    if args.smapi:
        if not all([args.client_id, args.client_secret, args.refresh_token, args.skill_id]):
            print("✗ --smapi requires --client-id, --client-secret, --refresh-token, --skill-id")
            sys.exit(1)
        import asyncio
        for locale in locales:
            print(f"\nTesting {locale} against SMAPI skill {args.skill_id}...")
            success = asyncio.run(test_smapi_build(
                args.client_id, args.client_secret,
                args.refresh_token, args.skill_id, locale,
            ))
            if not success:
                sys.exit(1)


if __name__ == "__main__":
    main()
