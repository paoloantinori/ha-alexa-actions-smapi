# Alexa Actionable Notifications via SMAPI

A Home Assistant custom integration that enables bidirectional communication with Alexa through actionable notifications. Users hear a question on their Echo device and respond by voice — Yes/No, text, a number, a selection, a duration, or a date/time — all without writing AWS Lambda code or managing cloud infrastructure.

Based on [keatontaylor/alexa-actions](https://github.com/keatontaylor/alexa-actions) with the SMAPI automation approach from [ha_alexa_proactive](https://github.com/user/ha_alexa_proactive).

## How It Works

The integration runs entirely inside Home Assistant. No AWS Lambda, no external hosting.

1. Your automation calls the `alexa_actions.send` service
2. The integration writes the notification payload to an `input_text` entity
3. It triggers the Alexa skill directly via `media_player.play_media` (SkillConnections.Launch API)
4. Alexa speaks the question on your Echo device
5. You respond by voice (yes, no, a number, etc.)
6. Amazon POSTs the response to an HTTPS webhook on your HA instance
7. The native skill handler fires an event on the HA event bus
8. Your automation branches on the response type

```
HA automation → alexa_actions.send → play_media("skill") → Alexa
                                                          ↓
HA event bus ← skill_handler ← HTTPS webhook ← Amazon POST
```

## Prerequisites

- **Home Assistant** with an external HTTPS URL (Nabu Casa, DuckDNS, etc.)
- **Amazon Developer Account** (free)
- **LWA Security Profile** (Login with Amazon — created in the Amazon Developer Console)
- **Alexa Media Player integration** installed and configured with at least one Echo device

## Setup

### 1. Create an LWA Security Profile

1. Go to the [Amazon Developer Console](https://developer.amazon.com/alexa/console/ask) → Security Profiles → Create
2. Set the **Redirect URI** to: `https://YOUR-HA-URL/auth/alexa_actions/callback`
3. Note the **Client ID** and **Client Secret**

### 2. Add the Integration in Home Assistant

1. Install via HACS (or copy `custom_components/alexa_actions/` to your HA config)
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for **"Alexa Actionable Notifications"**
4. Follow the config flow:
   - **Step 1**: Enter your LWA Client ID, Client Secret, and HA URL
   - **Step 2**: Click the link to authorize with Amazon (opens LWA consent screen)
   - **Step 3**: The integration automatically creates the Alexa skill and uploads the interaction model via SMAPI
   - **Step 4**: Confirm — done!

The integration handles skill creation, model upload, and enabling automatically. No manual Alexa Developer Console work required.

## Configuration

| Key | Required | Description |
|-----|----------|-------------|
| LWA Client ID | Yes | Security Profile ID from Amazon Developer Console |
| LWA Client Secret | Yes | Security Profile Secret |
| Home Assistant URL | Yes | External HTTPS URL of your HA instance |
| Long-Lived Access Token | Yes | HA token for the skill handler to read entity state |
| Invocation Name | No | Alexa skill invocation name (default: "actionable notifications") |
| Locales | No | Languages to support (auto-detected from HA config) |

## Usage

### Basic Yes/No Notification

```yaml
service: alexa_actions.send
data:
  text: "The front door has been opened. Did you open it?"
  event_id: "front_door_001"
target:
  entity_id: media_player.cucina
```

### With Predefined Options

```yaml
service: alexa_actions.send
data:
  text: "Which room should I turn on the lights in?"
  event_id: "lights_choice"
  options:
    - "Living Room"
    - "Bedroom"
    - "Kitchen"
target:
  entity_id: media_player.living_room
```

### With Suppressed Confirmation

```yaml
service: alexa_actions.send
data:
  text: "Your timer is done."
  event_id: "timer_done"
  suppress_confirmation: true
target:
  entity_id: media_player.kitchen
```

### With Custom Reprompt

Specify what Alexa says when the user doesn't respond clearly. If omitted, Alexa repeats the question.

```yaml
service: alexa_actions.send
data:
  text: "Did you take your medication today?"
  reprompt: "Sorry, I didn't catch that. Please say yes or no."
  event_id: "medication_reminder"
target:
  entity_id: media_player.bedroom
```

### With SSML (Rich Voice Formatting)

Wrap text in `<speak>` tags to use pauses, emphasis, whispers, and sound effects. See [Alexa SSML Reference](https://developer.amazon.com/en-US/docs/alexa/custom-skills/speech-synthesis-markup-language-ssml-reference.html).

```yaml
service: alexa_actions.send
data:
  text: "<speak>Paolo<break time='1s'/>hai preso la pastiglia?</speak>"
  reprompt: "<speak>Scusa<break time='500ms'/>rispondi sì o no.</speak>"
  event_id: "pastiglia"
target:
  entity_id: media_player.cucina
```

### Using the Blueprint (Wait-for-Response Pattern)

The included blueprint handles the full send → wait → branch flow:

1. Import `blueprints/alexa_actions_notification.yaml` in HA
2. Create a new automation from the blueprint
3. Choose a trigger entity, notification text, and target Alexa device
4. Add actions for each response type (Yes, No, Select, etc.)

The blueprint automatically:
- Generates a unique event ID
- Sends the notification
- Waits for the voice response (with configurable timeout)
- Branches based on `event_response_type`

### Handling Responses in Automations (Manual)

```yaml
automation:
  - trigger:
      - platform: event
        event_type: alexa_actionable_notification
        event_data:
          event_id: "front_door_001"
    action:
      - choose:
          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.event_response_type == 'ResponseYes' }}"
            sequence:
              - action: notify.mobile_app
                data:
                  message: "User confirmed they opened the door"
          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.event_response_type == 'ResponseNo' }}"
            sequence:
              - action: notify.mobile_app
                data:
                  message: "ALERT! User did NOT open the door!"
```

## Service Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `text` | Yes | The question or statement Alexa will speak. Supports SSML (wrap in `<speak>...</speak>`) |
| `alexa_device` | No* | `media_player` entity ID for the target Echo device |
| `event_id` | No | Unique ID for correlating responses (auto-generated if omitted) |
| `reprompt` | No | What Alexa says when the user hesitates (falls back to `text` if omitted) |
| `suppress_confirmation` | No | When true, Alexa skips "Okay" after the response (default: false) |
| `options` | No | List of predefined choices for the Select intent |

*\*Either `alexa_device` in data or `target.entity_id` must be specified.*

## Response Types

| Type | Key | Description |
|------|-----|-------------|
| Yes | `ResponseYes` | User said yes |
| No | `ResponseNo` | User said no |
| String | `ResponseString` | Free-text response |
| Select | `ResponseSelect` | User chose from predefined options |
| Number | `ResponseNumeric` | Numeric value |
| Duration | `ResponseDuration` | Duration in seconds |
| DateTime | `ResponseDateTime` | JSON with day/month/year/hour/minute/seconds |
| None | `ResponseNone` | Timeout or fallback |

## Supported Locales

de-DE, en-GB, en-US, es-ES, fr-CA, fr-FR, it-IT, pt-BR

## Troubleshooting

- **"No skill_id configured"**: The config flow did not complete successfully. Remove and re-add the integration.
- **"LWA error: invalid_client"**: Verify Client ID and Secret from the Amazon Developer Console.
- **"Skill not responding"**: Ensure your HA URL is externally accessible via HTTPS. Check that the `alexa_media` integration is configured with the target Echo device.
- **Alexa says something but no response event in HA**: Check HA logs for errors from `skill_handler`. Verify the long-lived access token is valid.
- **"alexa_device is required"**: Pass the Echo device either via `data.alexa_device` or `target.entity_id` in the service call.

## Development

```bash
# Run all tests
python -m pytest tests/ -v

# Run only unit tests
python -m pytest tests/test_skill_handler.py -v

# Run integration tests
python -m pytest tests/test_integration.py -v

# Run contract tests (service ↔ handler field agreement)
python -m pytest tests/test_config_contract.py -v
```

The integration has no external dependencies for testing — all HA modules are mocked via `conftest.py`.

## Credits

- Original Lambda handler: [keatontaylor/alexa-actions](https://github.com/keatontaylor/alexa-actions)
- SMAPI automation approach: [ha_alexa_proactive](https://github.com/user/ha_alexa_proactive)

## License

GPL-3.0 — see [LICENSE](LICENSE)
