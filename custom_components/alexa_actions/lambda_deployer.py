"""Lambda deployer that packages and deploys the Lambda function via boto3."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from homeassistant.core import HomeAssistant

from .exceptions import AWSDeploymentError

_LOGGER = logging.getLogger(__name__)

_DEFAULT_FUNCTION_NAME = "alexa-actionable-notifications"
_ROLE_NAME = "alexa-actions-lambda-role"
_RUNTIME = "python3.12"
_TIMEOUT = 30
_MEMORY = 128


class LambdaDeployer:
    """Deploys the Alexa actionable notifications Lambda function to AWS.

    Handles IAM role creation, zip packaging (with pip dependencies), and
    Lambda function creation or update.  All blocking boto3 / subprocess
    calls are executed in Home Assistant's executor thread via
    ``hass.async_add_executor_job()``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        aws_region: str,
    ) -> None:
        self._hass = hass
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key
        self._aws_region = aws_region

    # ------------------------------------------------------------------
    # boto3 client factory (sync — call from executor thread only)
    # ------------------------------------------------------------------

    def _get_boto3_client(self, service_name: str):
        """Return a boto3 client for *service_name* with stored credentials."""
        return boto3.client(
            service_name,
            aws_access_key_id=self._aws_access_key_id,
            aws_secret_access_key=self._aws_secret_access_key,
            region_name=self._aws_region,
        )

    # ------------------------------------------------------------------
    # IAM role
    # ------------------------------------------------------------------

    def _ensure_role(self) -> tuple[str, bool]:
        """Create or retrieve the IAM execution role.

        Returns (role_arn, was_created) so callers can decide whether
        to wait for IAM propagation.
        """
        iam = self._get_boto3_client("iam")

        # Reuse an existing role if one is already present.
        try:
            response = iam.get_role(RoleName=_ROLE_NAME)
            _LOGGER.debug("IAM role %s already exists", _ROLE_NAME)
            return response["Role"]["Arn"], False
        except ClientError:
            pass

        _LOGGER.info("Creating IAM role %s", _ROLE_NAME)
        response = iam.create_role(
            RoleName=_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            Description="Execution role for Alexa actionable notifications Lambda",
        )
        iam.attach_role_policy(
            RoleName=_ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        _LOGGER.info("Created IAM role %s", _ROLE_NAME)
        return response["Role"]["Arn"], True

    # ------------------------------------------------------------------
    # Zip packaging
    # ------------------------------------------------------------------

    def _find_lambda_dir(self) -> Path:
        """Locate the lambda/ source directory.

        Search order:
        1. ``custom_components/alexa_actions/lambda/`` (bundled inside the
           component when distributed via HACS).
        2. ``lambda/`` relative to the custom_components root (development
           layout where ``lambda/`` sits next to ``custom_components/``).
        """
        component_dir = Path(__file__).resolve().parent

        # Bundled inside the custom component package.
        bundled = component_dir / "lambda"
        if bundled.is_dir():
            return bundled

        # Development layout: project_root/lambda/
        dev_layout = component_dir.parent.parent.parent / "lambda"
        if dev_layout.is_dir():
            return dev_layout

        raise AWSDeploymentError(
            f"Lambda source directory not found. Searched: {bundled}, {dev_layout}"
        )

    def _install_deps(self, lambda_dir: Path, target: str) -> None:
        """Install Lambda dependencies into *target* via pip."""
        requirements = lambda_dir / "requirements.txt"
        if not requirements.exists():
            _LOGGER.debug("No requirements.txt found — skipping dependency install")
            return

        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            f"--target={target}",
            "-r",
            str(requirements),
        ]
        _LOGGER.debug("Installing Lambda dependencies: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise AWSDeploymentError(
                f"pip install failed (exit {result.returncode}): {result.stderr[:500]}"
            )

    def _build_zip(self) -> bytes:
        """Build the Lambda deployment zip in memory.

        The zip contains:
        - All ``.py`` files and ``language_strings.json`` from ``lambda/``
        - Installed pip dependencies from ``requirements.txt``
        """
        lambda_dir = self._find_lambda_dir()
        buf = io.BytesIO()

        with tempfile.TemporaryDirectory(prefix="alexa_actions_deps_") as tmp:
            # Install pip dependencies into a staging directory.
            self._install_deps(lambda_dir, tmp)

            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                # Add installed dependencies first.
                for root, __dirs, files in os.walk(tmp):
                    for fname in files:
                        full = os.path.join(root, fname)
                        arcname = os.path.relpath(full, tmp)
                        zf.write(full, arcname)

                # Add the Lambda source files (these take precedence over
                # any same-named files pulled in by pip).
                for item in lambda_dir.iterdir():
                    if item.suffix == ".py" or item.name == "language_strings.json":
                        zf.write(item, item.name)

        return buf.getvalue()

    # ------------------------------------------------------------------
    # Sync deployment logic (run in executor thread)
    # ------------------------------------------------------------------

    def _deploy_sync(
        self,
        zip_bytes: bytes,
        function_name: str,
        role_arn: str,
        environment: dict[str, str],
    ) -> str:
        """Create or update the Lambda function.  Returns the function ARN."""
        lambda_client = self._get_boto3_client("lambda")

        # Attempt to update an existing function first.
        try:
            existing = lambda_client.get_function(FunctionName=function_name)
            existing_arn = existing["Configuration"]["FunctionArn"]
            _LOGGER.info("Updating existing Lambda function: %s", function_name)

            lambda_client.update_function_code(
                FunctionName=function_name,
                ZipFile=zip_bytes,
            )
            lambda_client.update_function_configuration(
                FunctionName=function_name,
                Environment={"Variables": environment},
            )
            return existing_arn
        except ClientError:
            pass

        # Function does not exist — create it.
        _LOGGER.info("Creating new Lambda function: %s", function_name)
        response = lambda_client.create_function(
            FunctionName=function_name,
            Runtime=_RUNTIME,
            Role=role_arn,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=_TIMEOUT,
            MemorySize=_MEMORY,
            Environment={"Variables": environment},
        )
        return response["FunctionArn"]

    # ------------------------------------------------------------------
    # Public async entry point
    # ------------------------------------------------------------------

    async def async_deploy(
        self,
        home_assistant_url: str,
        ha_token: str,
        verify_ssl: bool = True,
        function_name: str | None = None,
    ) -> str:
        """Deploy the Lambda function.  Returns the Lambda ARN.

        All blocking I/O (file system, subprocess, boto3) is offloaded to
        Home Assistant's executor thread pool.
        """
        fn_name = function_name or _DEFAULT_FUNCTION_NAME

        environment = {
            "HOME_ASSISTANT_URL": home_assistant_url,
            "VERIFY_SSL": str(verify_ssl).lower(),
            "TOKEN": ha_token,
            "DEBUG": "false",
        }

        try:
            # Build the deployment zip (file I/O + subprocess).
            zip_bytes = await self._hass.async_add_executor_job(self._build_zip)

            # Ensure the IAM execution role exists.
            role_arn, role_created = await self._hass.async_add_executor_job(
                self._ensure_role
            )

            # Brief pause to allow IAM role propagation when a new role was
            # just created.  AWS recommends up to 10 s; 5 s is usually
            # sufficient in practice.  Skip the delay when reusing an
            # existing role.
            if role_created:
                await asyncio.sleep(5)

            # Create or update the Lambda function.
            arn = await self._hass.async_add_executor_job(
                self._deploy_sync, zip_bytes, fn_name, role_arn, environment
            )
            _LOGGER.info("Lambda deployed successfully: %s", arn)
            return arn

        except ClientError as err:
            self._raise_aws_error(err)
            raise  # unreachable, but satisfies type checker
        except AWSDeploymentError:
            raise
        except Exception as err:
            raise AWSDeploymentError(
                f"Lambda deployment failed: {err}"
            ) from err

    # ------------------------------------------------------------------
    # Error translation
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_aws_error(err: ClientError) -> None:
        """Translate a boto3 ClientError into an AWSDeploymentError."""
        code = err.response["Error"]["Code"]
        message = err.response["Error"]["Message"]

        if code == "AccessDeniedException":
            raise AWSDeploymentError(
                f"AWS access denied — check IAM permissions: {message}"
            ) from err
        if code == "ResourceLimitExceededException":
            raise AWSDeploymentError(
                f"AWS quota exceeded: {message}"
            ) from err
        if code in ("InvalidParameterValueException", "ValidationException"):
            raise AWSDeploymentError(
                f"Invalid AWS parameter: {message}"
            ) from err

        raise AWSDeploymentError(
            f"AWS deployment failed ({code}): {message}"
        ) from err
