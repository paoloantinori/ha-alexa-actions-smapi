# Alexa Actionable Notifications via SMAPI

A Home Assistant custom integration that enables bidirectional communication with Alexa through actionable notifications. Users can ask questions via Alexa and receive Yes/No, text, numeric, duration, date/time, or selection responses — all without writing AWS Lambda code or managing Alexa skill infrastructure manually.

Based on [keatontaylor/alexa-actions](https://github.com/keatontaylor/alexa-actions) with the SMAPI automation approach from [ha_alexa_proactive](https://github.com/user/ha_alexa_proactive).

## How It Works

1. You call the `alexa_actions.send` service from an automation
2. The integration writes the notification text to a Home Assistant entity
3. Your Alexa routine triggers the custom skill, which speaks the text
4. You respond by voice (yes, no, a number, etc.)
5. The AWS Lambda handler forwards your response back to Home Assistant
6. Your automation acts on the response

Architecture: HA → input_text entity → media_player triggers → Alexa → Lambda → HA event bus

## Prerequisites

- Home Assistant with external URL (HTTPS)
- Amazon Developer Account
- LWA (Login with Amazon) Security Profile
- AWS Account with Lambda + IAM permissions
- Alexa Media Player integration (for triggering the skill)

## Setup

### 1. Create LWA Security Profile
- Go to Amazon Developer Console → Security Profiles → Create
- Set redirect URI to: `https://YOUR-HA-URL/auth/alexa_actions/callback`
- Note the Client ID and Client Secret

### 2. Create AWS IAM User
- Create user with these permissions:
  - `lambda:CreateFunction`, `lambda:UpdateFunctionCode`, `lambda:UpdateFunctionConfiguration`, `lambda:GetFunction`
  - `iam:CreateRole`, `iam:AttachRolePolicy`, `iam:PassRole`, `iam:GetRole`
- Note the Access Key ID and Secret Access Key

### 3. Configure in Home Assistant
- Add the integration via Settings → Devices & Services → Add Integration
- Search for "Alexa Actions"
- Follow the 4-step config flow:
  1. Enter credentials
  2. Authorize with Amazon
  3. Wait for Lambda deployment + skill creation
  4. Confirm

### 4. Create Alexa Routine
- In the Alexa app, create a routine triggered by "When you say something"
- Add action: "Custom" → type your skill invocation name (e.g., "actionable notifications")

## Configuration

| Key | Required | Description |
|-----|----------|-------------|
| LWA Client ID | Yes | Security Profile ID from Amazon Developer Console |
| LWA Client Secret | Yes | Security Profile Secret |
| AWS Access Key ID | Yes | IAM user access key with Lambda+IAM permissions |
| AWS Secret Access Key | Yes | IAM user secret key |
| AWS Region | Yes | Region for Lambda deployment (e.g., us-east-1) |
| Home Assistant URL | Yes | External HTTPS URL of your HA instance |
| Long-Lived Access Token | Yes | HA token for Lambda to communicate with HA |
| Invocation Name | No | Alexa skill invocation name (default: "actionable notifications") |
| Locales | No | Languages to support (auto-detected from HA config) |

## Usage

### Basic Yes/No Notification
```yaml
service: alexa_actions.send
data:
  text: "The front door has been opened. Did you open it?"
  event_id: "front_door_001"
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
```

### With Suppressed Confirmation
```yaml
service: alexa_actions.send
data:
  text: "Your timer is done."
  event_id: "timer_done"
  suppress_confirmation: true
```

### Handling Responses in Automations
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

## Response Types

| Type | Response Key | Description |
|------|-------------|-------------|
| Yes | `ResponseYes` | User said yes |
| No | `ResponseNo` | User said no |
| String | `ResponseString` | Free-text response |
| Select | `ResponseSelect` | Predefined option selected |
| Number | `ResponseNumeric` | Numeric value |
| Duration | `ResponseDuration` | Duration in seconds |
| DateTime | `ResponseDateTime` | JSON with day/month/year/hour/minute/seconds |
| None | `ResponseNone` | Timeout or fallback |

## Troubleshooting

- **"Lambda source directory not found"**: Ensure the custom component includes the `lambda/` directory
- **"AWS access denied"**: Check IAM permissions include all required Lambda + IAM actions
- **"LWA error: invalid_client"**: Verify Client ID and Secret from Amazon Developer Console
- **"Skill not responding"**: Check the Alexa routine is configured with the correct invocation name

## Credits

- Original Lambda handler: [keatontaylor/alexa-actions](https://github.com/keatontaylor/alexa-actions)
- SMAPI automation approach: [ha_alexa_proactive](https://github.com/user/ha_alexa_proactive)

## License

GPL-3.0 — see [LICENSE](LICENSE)
