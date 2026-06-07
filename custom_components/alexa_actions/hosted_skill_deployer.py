"""Deploy Lambda code to an Alexa-hosted skill via git push.

Amazon provides a CodeCommit git repo for each hosted skill.  Pushing to
the ``master`` branch triggers a Lambda deployment.  This deployer clones
the repo, copies the Lambda source files alongside a baked ``config.json``,
and pushes the result.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile

from homeassistant.core import HomeAssistant

from .exceptions import HostedSkillError
from .paths import find_lambda_dir
from .smapi import SMAPI

_LOGGER = logging.getLogger(__name__)

# Files from lambda/ that must be copied into the hosted skill repo.
_LAMBDA_SOURCE_FILES = (
    "const.py",
    "lambda_function.py",
    "language_strings.json",
    "prompts.py",
    "schemas.py",
    "utils.py",
)


class HostedSkillDeployer:
    """Deploys Lambda code to an Alexa-hosted skill via git push.

    All blocking I/O (git subprocess calls) is offloaded to Home
    Assistant's executor thread pool.
    """

    def __init__(self, hass: HomeAssistant, smapi: SMAPI) -> None:
        self._hass = hass
        self._smapi = smapi

    # ------------------------------------------------------------------
    # Public async entry point
    # ------------------------------------------------------------------

    async def async_push_lambda_code(
        self,
        skill_id: str,
        ha_url: str,
        ha_token: str,
        verify_ssl: bool = True,
    ) -> None:
        """Push Lambda source + baked config.json to the hosted skill repo.

        Steps:
        1. Get repo URL and generate temporary git credentials via SMAPI.
        2. Clone the repo into a temp directory.
        3. Copy Lambda source files and write ``config.json``.
        4. ``git add && git commit && git push origin master``.
        """
        # Pre-check git availability before any SMAPI calls.
        if shutil.which("git") is None:
            raise HostedSkillError(
                "git is not installed — required for Alexa-hosted skill"
                " deployment. Install git on your Home Assistant host."
            )

        # Step 1: Fetch repo metadata and git credentials concurrently.
        repo_meta, (username, password) = await asyncio.gather(
            self._smapi.async_get_hosted_repo_metadata(skill_id),
            self._smapi.async_generate_git_credentials(skill_id),
        )

        repo_url = repo_meta.get("repository", {}).get("url", "")
        if not repo_url:
            raise HostedSkillError(
                "Hosted skill repo URL not found in SMAPI response"
            )

        # Inject credentials into the repo URL for authenticated clone.
        auth_url = repo_url.replace(
            "https://", f"https://{username}:{password}@", 1,
        )

        # Step 2-4: all blocking git work in executor thread.
        await self._hass.async_add_executor_job(
            self._push_sync,
            auth_url,
            ha_url,
            ha_token,
            verify_ssl,
        )

        _LOGGER.info("Lambda code pushed to hosted skill %s", skill_id)

    # ------------------------------------------------------------------
    # Sync implementation (executor thread only)
    # ------------------------------------------------------------------

    def _push_sync(
        self,
        auth_url: str,
        ha_url: str,
        ha_token: str,
        verify_ssl: bool,
    ) -> None:
        """Blocking: clone, copy files, commit, push."""
        try:
            lambda_dir = find_lambda_dir()
        except FileNotFoundError as err:
            raise HostedSkillError(str(err)) from err

        with tempfile.TemporaryDirectory(
            prefix="alexa_hosted_skill_",
        ) as tmpdir:
            repo_dir = os.path.join(tmpdir, "repo")

            # Shallow clone — we only need the latest state.
            self._run_git(
                ["git", "clone", "--depth", "1", auth_url, repo_dir],
                cwd=tmpdir,
                label="clone",
            )

            # Copy Lambda source files.
            lambda_subdir = os.path.join(repo_dir, "lambda")
            os.makedirs(lambda_subdir, exist_ok=True)

            for fname in _LAMBDA_SOURCE_FILES:
                src = lambda_dir / fname
                if not src.exists():
                    raise HostedSkillError(
                        f"Lambda source file not found: {src}"
                    )
                shutil.copy2(str(src), os.path.join(lambda_subdir, fname))

            # Also copy requirements.txt if it exists.
            req_file = lambda_dir / "requirements.txt"
            if req_file.exists():
                shutil.copy2(
                    str(req_file),
                    os.path.join(lambda_subdir, "requirements.txt"),
                )

            # Write config.json alongside the handler.
            config = {
                "HOME_ASSISTANT_URL": ha_url,
                "TOKEN": ha_token,
                "VERIFY_SSL": str(verify_ssl).lower(),
                "DEBUG": "false",
            }
            config_path = os.path.join(lambda_subdir, "config.json")
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump(config, fh, indent=2)

            # Commit and push.
            self._run_git(
                ["git", "add", "-A"],
                cwd=repo_dir,
                label="add",
            )
            self._run_git(
                [
                    "git",
                    "-c", "user.email=alexa-actions@homeassistant.local",
                    "-c", "user.name=HA Alexa Actions",
                    "commit", "-m", "Update Lambda code from Home Assistant",
                    "--allow-empty",
                ],
                cwd=repo_dir,
                label="commit",
            )
            self._run_git(
                ["git", "push", "origin", "master"],
                cwd=repo_dir,
                label="push",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_git(
        cmd: list[str],
        *,
        cwd: str,
        label: str,
        timeout: int = 120,
    ) -> str:
        """Run a git subprocess and return stdout. Raises on failure."""
        _LOGGER.debug("git %s in %s", label, cwd)
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise HostedSkillError(
                f"git {label} failed (exit {result.returncode}):"
                f" {stderr[:500]}"
            )
        return result.stdout
