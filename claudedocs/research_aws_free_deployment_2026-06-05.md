# Research: Can Alexa Skills Be Deployed Without AWS IAM Credentials?

**Date**: 2026-06-05
**Confidence**: High (primary sources from Amazon developer documentation)

## Executive Summary

**Yes, it is possible** to eliminate AWS IAM credentials from the user setup by switching from self-hosted Lambda to **Alexa-Hosted Skills**. Amazon provides free Lambda hosting for Alexa skills with no AWS account required. The entire provisioning can be done through SMAPI using only LWA (Login with Amazon) OAuth credentials.

However, the deployment model changes significantly — from "upload a zip via boto3" to "git push to an Amazon-managed CodeCommit repo." This has trade-offs.

---

## Three Approaches Compared

### Approach A: Current (Self-Hosted Lambda via IAM)
- **User needs**: LWA security profile + AWS IAM user with Lambda/IAM permissions
- **Deploy mechanism**: boto3 creates IAM role + uploads zip to Lambda
- **Pros**: Full control over Lambda env vars, region, memory, timeout
- **Cons**: High setup friction (2 separate cloud accounts)

### Approach B: Alexa-Hosted Skills (via SMAPI + Git)
- **User needs**: LWA security profile ONLY
- **Deploy mechanism**: SMAPI creates hosted skill → git push code to Amazon's CodeCommit repo
- **Pros**: Zero AWS account needed; Amazon provides free Lambda (3 free skills, 1GB storage each)
- **Cons**: Cannot customize Lambda env vars at deploy time; code is deployed via git, not API upload

### Approach C: HTTPS Webhook Endpoint (No Lambda at All)
- **User needs**: LWA security profile ONLY
- **Deploy mechanism**: Skill manifest points at an HTTPS endpoint (the HA instance itself)
- **Pros**: No Lambda, no AWS, no git — HA IS the endpoint
- **Cons**: HA must be internet-accessible; latency depends on user's network; requires SSL cert

---

## Detailed Analysis: Approach B (Alexa-Hosted Skills)

### How It Works

1. **Create hosted skill via SMAPI**: `POST /v1/skills` with a manifest that includes `hostedSkillProvisioning` — Amazon auto-provisions a Lambda function and a CodeCommit git repo ([Skill Manifest REST API](https://developer.amazon.com/en-US/docs/alexa/smapi/skill-operations.html))

2. **Wait for provisioning**: Check skill status via `GET /v1/skills/{skillId}/status` until `hostedSkillProvisioning` shows `SUCCEEDED` ([ASK CLI Reference](https://developer.amazon.com/en-US/docs/alexa/smapi/ask-cli-command-reference.html))

3. **Get git credentials**: `POST /v1/skills/{skillId}/alexaHosted/repository/credentials/generate` returns temporary username/password ([Alexa-Hosted Skill API](https://developer.amazon.com/en-IN/docs/alexa/smapi/alexa-hosted-skill.html))

4. **Get repo URL**: `GET /v1/skills/{skillId}/alexaHosted` returns the git repository URL

5. **Git push code**: Clone the repo, replace the code with our Lambda handler, `git push` to `master` branch deploys to the development Lambda ([Alexa-Hosted Skills with ASK CLI](https://developer.amazon.com/en-US/docs/alexa/hosted-skills/alexa-hosted-skills-ask-cli.html))

### Key Limitation for Our Use Case

The Lambda function for an Alexa-hosted skill receives its configuration differently:
- **No custom environment variables**: The Lambda env vars (`HOME_ASSISTANT_URL`, `TOKEN`, `VERIFY_SSL`) cannot be set through SMAPI or the console. The code in the hosted repo is static.
- **Workaround options**:
  1. **Write config into the code itself** before git push — generate a `config.json` file and commit it alongside the Lambda handler
  2. **Use Alexa settings API** to store user config and have the Lambda read it at runtime
  3. **Use the HA URL as a webhook** — the Lambda becomes a thin proxy that calls back to HA for all logic

### Free Tier
- Up to 3 Alexa-hosted skills per developer account
- 1 GB code storage per skill
- AWS Lambda free tier (1M requests/month, 400,000 GB-seconds)
- No AWS account needed

---

## Detailed Analysis: Approach C (HTTPS Webhook — HA as Endpoint)

### How It Works

The skill manifest's `apis.custom.endpoint` can point to any HTTPS URL instead of a Lambda ARN:

```json
{
  "apis": {
    "custom": {
      "endpoint": {
        "uri": "https://my-ha.example.com/api/alexa_actions/webhook"
      }
    }
  }
}
```

The HA integration would expose a webhook endpoint that receives Alexa requests directly, completely eliminating Lambda and AWS.

### Pros
- **Zero cloud infrastructure** — no Lambda, no AWS, no git
- **Simplest possible deployment** — just LWA + HA URL
- **Full control** — env vars are irrelevant, HA config entry has everything
- **Low latency** — no Lambda cold start

### Cons
- **HA must be internet-accessible** — needs Nabu Casa, a reverse proxy, or similar
- **SSL required** — Alexa only calls HTTPS endpoints with valid certificates
- **Many HA users already have this** — Nabu Casa provides the URL automatically

---

## Recommendation

**Approach C (HTTPS webhook)** is the best option for a Home Assistant integration because:

1. **Most HA users already have an externally accessible URL** via Nabu Casa Cloud or a reverse proxy
2. **Eliminates ALL cloud credentials** — only LWA is needed for SMAPI (to register the skill and upload interaction models)
3. **The Lambda function we deploy is just a thin proxy** — it receives Alexa requests and calls back to HA. Cutting out the middleman simplifies everything
4. **No git dependency in HA** — running `git push` from inside a HA custom integration is fragile and unusual

The flow would become:
1. User creates LWA security profile (same as now)
2. HA config flow collects: LWA client ID/secret + HA URL (auto-detected via Nabu Casa)
3. Integration creates skill via SMAPI with `endpoint.uri` pointing at HA webhook
4. Integration uploads interaction models via SMAPI
5. Integration enables the skill
6. **Done** — no AWS, no Lambda, no git

### Migration Path

The current Lambda-based approach (Approach A) can remain as a fallback for users who want Lambda (e.g., HA not exposed to internet). The config flow could offer a choice:
- **"Direct connection"** (recommended) — HA is the endpoint, no AWS needed
- **"AWS Lambda relay"** — current approach, for users behind NAT without Nabu Casa

---

## Sources

1. [SMAPI Overview](https://developer.amazon.com/en-US/docs/alexa/smapi/smapi-overview.html) — LWA authentication for all SMAPI operations
2. [Alexa-Hosted Skill Management REST API](https://developer.amazon.com/en-IN/docs/alexa/smapi/alexa-hosted-skill.html) — Git-based deployment, credentials generation
3. [Skill Manifest REST API](https://developer.amazon.com/en-US/docs/alexa/smapi/skill-operations.html) — `hostedSkillProvisioning` status, skill creation
4. [ASK CLI Command Reference](https://developer.amazon.com/en-US/docs/alexa/smapi/ask-cli-command-reference.html) — `hostedSkillProvisioning` resource type
5. [About Alexa-Hosted Skills](https://developer.amazon.com/en-US/docs/alexa/hosted-skills/build-a-skill-end-to-end-using-an-alexa-hosted-skill.html) — Free tier, limitations, provisioning
6. [Host a Custom Skill as an AWS Lambda Function](https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-an-aws-lambda-function.html) — Lambda region mapping
7. [ASK CLI Hosted Skills](https://developer.amazon.com/en-US/docs/alexa/hosted-skills/alexa-hosted-skills-ask-cli.html) — Git push deploys to development Lambda
