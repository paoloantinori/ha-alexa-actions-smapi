"""Tests for the hosted-skill SMAPI methods and HostedSkillDeployer."""
import sys
import os

# Ensure custom_components is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

# Mock HA and aiohttp modules before importing
_ha_mocks = {
    "homeassistant.core": MagicMock(),
    "homeassistant.config_entries": MagicMock(),
    "homeassistant.const": MagicMock(),
    "homeassistant.data_entry_flow": MagicMock(),
    "homeassistant.helpers": MagicMock(),
    "homeassistant.helpers.selector": MagicMock(),
    "homeassistant.helpers.network": MagicMock(),
    "homeassistant.components": MagicMock(),
    "homeassistant.components.http": MagicMock(),
    "aiohttp": MagicMock(),
}
for _name, _mock in _ha_mocks.items():
    sys.modules.setdefault(_name, _mock)

# Use a real exception class so isinstance() and pytest.raises() work.
class HomeAssistantError(Exception):
    pass

sys.modules["homeassistant.exceptions"] = MagicMock(HomeAssistantError=HomeAssistantError)

import pytest

from custom_components.alexa_actions.smapi import SMAPI
from custom_components.alexa_actions.exceptions import SMAPIError, HostedSkillError
from custom_components.alexa_actions.hosted_skill_deployer import HostedSkillDeployer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lwa_client() -> MagicMock:
    """Return a mock LWAClient with async methods."""
    lwa = MagicMock()
    lwa.async_get_smapi_token = AsyncMock(return_value="Bearer test_token")
    lwa.get_session = AsyncMock()
    return lwa


def _run(coro):
    """Run an async coroutine synchronously in a fresh event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# SMAPI hosted-skill method tests
# ---------------------------------------------------------------------------


class TestCreateHostedSkill:
    """Tests for SMAPI.async_create_hosted_skill."""

    def test_returns_skill_id(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        smapi._async_request = AsyncMock(
            return_value={"skillId": "amzn1.ask.skill.12345"}
        )

        result = _run(
            smapi.async_create_hosted_skill("vendor_abc", "my skill", ["en-US"])
        )
        assert result == "amzn1.ask.skill.12345"

    def test_manifest_has_no_endpoint(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        smapi._async_request = AsyncMock(
            return_value={"skillId": "amzn1.ask.skill.999"}
        )

        _run(smapi.async_create_hosted_skill("vendor_abc"))

        call_args = smapi._async_request.call_args
        manifest = call_args[1]["json"]["manifest"]
        # Hosted skill manifest should NOT have an endpoint key
        assert "endpoint" not in manifest["apis"]["custom"]

    def test_raises_on_missing_skill_id(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        smapi._async_request = AsyncMock(return_value={})

        with pytest.raises(SMAPIError, match="did not return a skill ID"):
            _run(smapi.async_create_hosted_skill("vendor_abc"))


class TestWaitForHostedProvisioning:
    """Tests for SMAPI.async_wait_for_hosted_provisioning."""

    def test_succeeds_on_succeeded_status(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        smapi.async_get_skill_status = AsyncMock(
            return_value={
                "hostedSkillProvisioning": {
                    "lastUpdateRequest": {"status": "SUCCEEDED"}
                }
            }
        )

        # Should not raise
        _run(
            smapi.async_wait_for_hosted_provisioning(
                "skill_123", timeout=5, poll_interval=0.1,
            )
        )

    def test_raises_on_failed_status(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        smapi.async_get_skill_status = AsyncMock(
            return_value={
                "hostedSkillProvisioning": {
                    "lastUpdateRequest": {"status": "FAILED"}
                }
            }
        )

        with pytest.raises(SMAPIError, match="provisioning failed"):
            _run(
                smapi.async_wait_for_hosted_provisioning(
                    "skill_123", timeout=5, poll_interval=0.1,
                )
            )

    def test_raises_on_timeout(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        # Always return IN_PROGRESS
        smapi.async_get_skill_status = AsyncMock(
            return_value={
                "hostedSkillProvisioning": {
                    "lastUpdateRequest": {"status": "IN_PROGRESS"}
                }
            }
        )

        with pytest.raises(SMAPIError, match="timed out"):
            _run(
                smapi.async_wait_for_hosted_provisioning(
                    "skill_123", timeout=1, poll_interval=0.2,
                )
            )


class TestGetHostedRepoMetadata:
    """Tests for SMAPI.async_get_hosted_repo_metadata."""

    def test_returns_metadata(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        expected = {
            "repository": {
                "url": "https://git-codecommit.us-east-1.amazonaws.com/v1/repos/skill-abc",
            },
        }
        smapi._async_request = AsyncMock(return_value=expected)

        result = _run(smapi.async_get_hosted_repo_metadata("skill_abc"))
        assert result == expected

    def test_raises_on_invalid_response(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        smapi._async_request = AsyncMock(return_value=None)

        with pytest.raises(SMAPIError, match="Unexpected response"):
            _run(smapi.async_get_hosted_repo_metadata("skill_abc"))


class TestGenerateGitCredentials:
    """Tests for SMAPI.async_generate_git_credentials."""

    def test_returns_username_password(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        smapi._async_request = AsyncMock(
            return_value={"username": "user-123", "password": "pass-456"}
        )

        username, password = _run(
            smapi.async_generate_git_credentials("skill_abc")
        )
        assert username == "user-123"
        assert password == "pass-456"

    def test_raises_on_missing_fields(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        smapi._async_request = AsyncMock(return_value={"username": "u"})

        with pytest.raises(SMAPIError, match="missing fields"):
            _run(smapi.async_generate_git_credentials("skill_abc"))


class TestBuildManifestHosted:
    """Tests for _build_manifest with empty lambda_arn (hosted mode)."""

    def test_no_endpoint_when_arn_empty(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        manifest = smapi._build_manifest("", "my skill", ["en-US"])
        assert "endpoint" not in manifest["apis"]["custom"]

    def test_endpoint_present_when_arn_provided(self):
        lwa = _make_lwa_client()
        smapi = SMAPI(lwa)
        manifest = smapi._build_manifest(
            "arn:aws:lambda:us-east-1:123:function:fn", "my skill"
        )
        assert manifest["apis"]["custom"]["endpoint"]["sourceArn"] == (
            "arn:aws:lambda:us-east-1:123:function:fn"
        )


# ---------------------------------------------------------------------------
# HostedSkillDeployer tests
# ---------------------------------------------------------------------------


class TestHostedSkillDeployerGitCheck:
    """Tests for git availability check."""

    def test_raises_when_git_not_found(self):
        hass = MagicMock()
        smapi = MagicMock()
        deployer = HostedSkillDeployer(hass, smapi)

        with patch("custom_components.alexa_actions.hosted_skill_deployer.shutil.which", return_value=None):
            with pytest.raises(HostedSkillError, match="git is not installed"):
                _run(
                    deployer.async_push_lambda_code(
                        skill_id="skill_123",
                        ha_url="https://ha.local",
                        ha_token="token",
                    )
                )
