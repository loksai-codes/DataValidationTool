"""
Secret / variable interpolation for project files.

Supported placeholders (resolved anywhere in the YAML, recursively):

    ${env:NAME}                    environment variable (error if unset)
    ${env:NAME:-default}           environment variable with default
    ${NAME}                        shorthand for ${env:NAME}
    ${file:/path/to/secret}        file contents (trailing newline stripped)
    ${aws_secret:secret-id}        AWS Secrets Manager SecretString
    ${aws_secret:secret-id#key}    ...JSON field inside the secret
    ${alibaba_kms:secret-name}     Alibaba Cloud KMS Secrets Manager
    ${alibaba_kms:secret-name#key} ...JSON field inside the secret

Cloud resolvers import their SDK lazily, so they are only needed when used.
Resolved values are registered with the log masking filter so they never reach logs.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, Optional

from datavalidator.log import register_secret

_PATTERN = re.compile(r"\$\{([^}]+)\}")


class SecretResolutionError(RuntimeError):
    pass


def _split_key(ref: str):
    if "#" in ref:
        name, key = ref.split("#", 1)
        return name, key
    return ref, None


def _pick(raw: str, key: Optional[str], ref: str) -> str:
    if key is None:
        return raw
    try:
        return str(json.loads(raw)[key])
    except Exception as exc:  # noqa: BLE001
        raise SecretResolutionError(f"Secret '{ref}' has no JSON key '{key}'") from exc


def _env(ref: str) -> str:
    if ":-" in ref:
        name, default = ref.split(":-", 1)
        return os.environ.get(name, default)
    if ref not in os.environ:
        raise SecretResolutionError(f"Environment variable '{ref}' is not set")
    return os.environ[ref]


def _file(ref: str) -> str:
    with open(os.path.expanduser(ref), "r", encoding="utf-8") as fh:
        return fh.read().rstrip("\n")


_aws_cache: Dict[str, str] = {}


def _aws_secret(ref: str) -> str:
    name, key = _split_key(ref)
    if name not in _aws_cache:
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise SecretResolutionError("boto3 is required for ${aws_secret:...}; pip install 'datavalidator[aws]'") from exc
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        client = boto3.client("secretsmanager", region_name=region)
        _aws_cache[name] = client.get_secret_value(SecretId=name)["SecretString"]
    return _pick(_aws_cache[name], key, ref)


_ali_cache: Dict[str, str] = {}


def _alibaba_kms(ref: str) -> str:
    name, key = _split_key(ref)
    if name not in _ali_cache:
        try:
            from alibabacloud_kms20160120 import models as kms_models  # type: ignore
            from alibabacloud_kms20160120.client import Client  # type: ignore
            from alibabacloud_tea_openapi import models as open_api_models  # type: ignore
        except ImportError as exc:
            raise SecretResolutionError(
                "alibabacloud_kms20160120 is required for ${alibaba_kms:...}; pip install 'datavalidator[alibaba]'"
            ) from exc
        region = os.environ.get("ALIBABA_CLOUD_REGION_ID", "cn-hangzhou")
        cfg = open_api_models.Config(
            access_key_id=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"),
            access_key_secret=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET"),
            endpoint=os.environ.get("ALIBABA_CLOUD_KMS_ENDPOINT", f"kms.{region}.aliyuncs.com"),
        )
        resp = Client(cfg).get_secret_value(kms_models.GetSecretValueRequest(secret_name=name))
        _ali_cache[name] = resp.body.secret_data
    return _pick(_ali_cache[name], key, ref)


RESOLVERS: Dict[str, Callable[[str], str]] = {
    "env": _env,
    "file": _file,
    "aws_secret": _aws_secret,
    "alibaba_kms": _alibaba_kms,
}

_SECRET_SCHEMES = {"file", "aws_secret", "alibaba_kms"}
_SECRET_FIELD_HINTS = ("password", "secret", "token", "key_secret", "access_key")


def _resolve_token(token: str) -> tuple[str, bool]:
    if ":" in token:
        scheme, ref = token.split(":", 1)
        if scheme in RESOLVERS:
            return RESOLVERS[scheme](ref), scheme in _SECRET_SCHEMES
    return _env(token), False


def resolve(value: Any, field_name: str = "") -> Any:
    """Recursively resolve placeholders in a parsed YAML structure."""
    if isinstance(value, dict):
        return {k: resolve(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, field_name) for v in value]
    if not isinstance(value, str) or "${" not in value:
        return value

    is_secret_field = any(h in field_name.lower() for h in _SECRET_FIELD_HINTS)

    def repl(m: re.Match) -> str:
        resolved, is_secret = _resolve_token(m.group(1).strip())
        if is_secret or is_secret_field:
            register_secret(resolved)
        return resolved

    return _PATTERN.sub(repl, value)
