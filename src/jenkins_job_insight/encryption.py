"""Symmetric encryption for sensitive fields stored in the database.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the ``cryptography`` package.
The encryption key is derived from the ``JJI_ENCRYPTION_KEY`` environment
variable.  When the variable is unset, a random key is auto-generated on
first use and persisted to ``~/.local/share/jji/.encryption_key`` (or
``$XDG_DATA_HOME/jji/.encryption_key``).  The key file is created with
mode 0600 so that only the owning user can read it.

Sensitive fields (passwords, tokens, emails) are encrypted before being
written to ``request_params`` in the database and decrypted when the params
are read back during job resumption.
"""

import base64
import hashlib
import os
import re
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from simple_logger.logger import get_logger

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))

# Fields in ``request_params`` that contain secrets and must be encrypted.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "jenkins_password",
        "jenkins_user",
        "jira_api_token",
        "jira_pat",
        "jira_email",
        "jira_token",
        "github_token",
        "reportportal_api_token",
    }
)

# Prefix prepended to every ciphertext so that ``decrypt_sensitive_fields``
# can distinguish already-encrypted values from legacy plaintext values.
_ENCRYPTED_PREFIX = "enc:"

# Length of the key produced by ``secrets.token_urlsafe(32)``.
_KEY_BYTES = 32
_FILE_KEY_LENGTH = len(secrets.token_urlsafe(_KEY_BYTES))
_FILE_KEY_RE = re.compile(rf"^[A-Za-z0-9_-]{{{_FILE_KEY_LENGTH}}}$")


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a URL-safe 32-byte key suitable for Fernet from *secret*."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _read_key_file(key_file: Path) -> str:
    """Read and validate an encryption key from *key_file*.

    Raises:
        RuntimeError: If the file is empty, malformed, or has unexpected length.
    """
    key = key_file.read_text(encoding="utf-8").strip()
    if not _FILE_KEY_RE.fullmatch(key):
        raise RuntimeError(f"Encryption key file {key_file} is empty or corrupt")
    return key


def _read_key_file_when_ready(
    key_file: Path, retries: int = 20, delay_seconds: float = 0.05
) -> str:
    """Read an encryption key, retrying briefly for a racing writer to finish.

    Another process may have created the file but not yet written the key.
    We retry with short sleeps before treating the file as corrupt.
    """
    for attempt in range(retries):
        try:
            return _read_key_file(key_file)
        except RuntimeError:
            if attempt == retries - 1:
                raise
            time.sleep(delay_seconds)
    # Unreachable, but satisfies type checkers.
    raise RuntimeError(
        f"Encryption key file {key_file} is empty or corrupt"
    )  # pragma: no cover


def _ensure_private_key_file(key_file: Path) -> None:
    """Tighten permissions to 0600 if the file is group/world-readable."""
    if key_file.stat().st_mode & 0o077:
        key_file.chmod(0o600)


def _get_or_create_key_file() -> str:
    """Return a persistent random key from a local file, creating it on first use.

    The key file is stored at ``$XDG_DATA_HOME/jji/.encryption_key``
    (defaults to ``~/.local/share/jji/.encryption_key``) and is only
    readable by the owning user (mode 0600).
    """
    key_dir = (
        Path(os.environ.get("XDG_DATA_HOME", ""))
        if os.environ.get("XDG_DATA_HOME")
        else Path.home() / ".local" / "share"
    ) / "jji"
    key_file = key_dir / ".encryption_key"
    if key_file.exists():
        _ensure_private_key_file(key_file)
        return _read_key_file_when_ready(key_file)
    key_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(_KEY_BYTES)
    # Use O_CREAT|O_EXCL for atomic exclusive creation to avoid TOCTOU races.
    # If another worker wins the race, os.open raises FileExistsError and we
    # fall back to reading the file they created.
    try:
        fd = os.open(str(key_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key)
    except FileExistsError:
        _ensure_private_key_file(key_file)
        # Another worker created the file first — use their key.
        return _read_key_file_when_ready(key_file)
    return key


def _get_fernet() -> Fernet:
    """Return a ``Fernet`` instance using the configured encryption key.

    Key resolution order:
    1. ``JJI_ENCRYPTION_KEY`` environment variable (recommended for production).
    2. Auto-generated file-based key (``~/.local/share/jji/.encryption_key``).
    """
    secret = os.environ.get("JJI_ENCRYPTION_KEY", "")
    if not secret:
        secret = _get_or_create_key_file()
    return Fernet(_derive_fernet_key(secret))


def encrypt_sensitive_fields(params: dict) -> dict:
    """Return a shallow copy of *params* with sensitive values encrypted.

    Only non-empty string values listed in :data:`SENSITIVE_KEYS` are
    encrypted.  Values are prefixed with ``enc:`` so that
    :func:`decrypt_sensitive_fields` can distinguish them from legacy
    plaintext values.

    The Fernet instance is lazily initialised: when no sensitive field
    carries a non-empty string value, the key file is never touched.
    """
    result = dict(params)
    fernet: Fernet | None = None
    for key in SENSITIVE_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value and not value.startswith(_ENCRYPTED_PREFIX):
            if fernet is None:
                fernet = _get_fernet()
            token = fernet.encrypt(value.encode()).decode()
            result[key] = f"{_ENCRYPTED_PREFIX}{token}"
    return result


# Fields stripped from ``request_params`` before returning data to API consumers.
# Derived from :data:`SENSITIVE_KEYS` to avoid duplicating the allowlist.
# Extend this set (via ``|``) if additional non-encrypted but private fields
# need to be redacted without triggering encryption overhead.
RESPONSE_REDACTED_KEYS: frozenset[str] = frozenset(SENSITIVE_KEYS)


def strip_sensitive_from_response(result_data: dict) -> dict:
    """Remove sensitive fields from ``request_params`` before returning to API consumers.

    The encrypted values remain in the database for job resumption but are
    never exposed in HTTP responses.

    Args:
        result_data: Parsed result dictionary (may contain ``request_params``).

    Returns:
        A shallow copy with redacted ``request_params``, or the original dict
        unchanged when ``request_params`` is absent.
    """
    if not result_data or "request_params" not in result_data:
        return result_data
    request_params = result_data.get("request_params")
    if request_params is None:
        return result_data
    result = dict(result_data)
    if not isinstance(request_params, dict):
        result.pop("request_params", None)
        return result
    params = dict(request_params)
    for key in RESPONSE_REDACTED_KEYS:
        params.pop(key, None)
    result["request_params"] = params
    return result


def encrypt_value(value: str) -> str:
    """Encrypt a single string value. Returns prefixed ciphertext."""
    if not value:
        return ""
    fernet = _get_fernet()
    token = fernet.encrypt(value.encode()).decode()
    return f"{_ENCRYPTED_PREFIX}{token}"


def decrypt_value(value: str) -> str:
    """Decrypt a single string value. Returns plaintext, or empty string on failure."""
    if not value or not value.startswith(_ENCRYPTED_PREFIX):
        return value or ""
    fernet = _get_fernet()
    ciphertext = value[len(_ENCRYPTED_PREFIX) :]
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.warning("Failed to decrypt value: encryption key may have changed")
        return ""


def decrypt_sensitive_fields(params: dict) -> dict:
    """Return a shallow copy of *params* with sensitive values decrypted.

    Values that do not carry the ``enc:`` prefix are assumed to be legacy
    plaintext and are returned as-is.  Decryption failures (e.g. key change)
    are logged and the raw ciphertext is preserved so the caller can still
    detect that a value was present.

    The Fernet instance is lazily initialised: when no encrypted value is
    present, the key file is never touched.
    """
    result = dict(params)
    fernet: Fernet | None = None
    for key in SENSITIVE_KEYS:
        value = result.get(key)
        if value and isinstance(value, str) and value.startswith(_ENCRYPTED_PREFIX):
            if fernet is None:
                fernet = _get_fernet()
            ciphertext = value[len(_ENCRYPTED_PREFIX) :]
            try:
                result[key] = fernet.decrypt(ciphertext.encode()).decode()
            except InvalidToken:
                logger.warning(
                    f"Failed to decrypt field '{key}': encryption key may have changed. "
                    "The encrypted value will be kept as-is."
                )
    return result
