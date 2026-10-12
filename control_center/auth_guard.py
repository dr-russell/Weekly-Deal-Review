"""Persist authentication failures until an explicit, successful reconnection."""
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession as GoogleSession

LOCK = threading.RLock()


class ReconnectRequired(RuntimeError):
    pass


def marker_path(token_path):
    return Path(str(token_path) + ".auth-block.json")


def reconnect_message(connection):
    command = "--gmail" if connection == "gmail" else "--reauthorize"
    name = "Gmail" if connection == "gmail" else "Google Sheets/Docs"
    return (f"{name} authentication is blocked after a failure. No further requests will be made. "
            f"Explicitly reconnect with control_center/.venv/bin/python -m control_center.auth {command}.")


def check_connection(token_path, connection):
    try:
        # Even an unreadable or damaged marker must not silently enable access.
        marker_path(token_path).read_text()
    except FileNotFoundError:
        return
    except OSError:
        raise ReconnectRequired(reconnect_message(connection)) from None
    raise ReconnectRequired(reconnect_message(connection))


def block_connection(token_path, connection, reason):
    with LOCK:
        path = marker_path(token_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Store only a controlled category, never provider messages or credentials.
        value = {"connection": connection, "reason": reason,
                 "at": datetime.now(timezone.utc).isoformat()}
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".auth-block-")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(value, handle)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def complete_reconnection(token_path):
    """Call only after explicit authorization, account verification and token save."""
    with LOCK:
        marker_path(token_path).unlink(missing_ok=True)


def refresh_credentials(creds, request, token_path, connection):
    with LOCK:
        check_connection(token_path, connection)
        try:
            creds.refresh(request)
        except RefreshError as error:
            if error.retryable:
                raise
            block_connection(token_path, connection, "token_refresh_rejected")
            raise ReconnectRequired(reconnect_message(connection)) from None


def authentication_response(response):
    if response.status_code == 401:
        return True
    if response.status_code != 403:
        return False
    try:
        error = response.json().get("error", {})
        return (error.get("status") == "UNAUTHENTICATED" or
                any(item.get("reason") in {"authError", "insufficientPermissions"}
                    for item in error.get("errors", [])) or
                any(item.get("reason") in {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "CREDENTIALS_MISSING"}
                    for item in error.get("details", [])))
    except (ValueError, AttributeError, TypeError):
        return False


class AuthorizedSession(GoogleSession):
    def __init__(self, credentials, *, token_path, connection, reconnecting=False):
        super().__init__(credentials, max_refresh_attempts=0)
        self.token_path = token_path
        self.connection = connection
        self.reconnecting = reconnecting

    def request(self, *args, **kwargs):
        # Serialize credential use so queued requests stop after the first failure.
        with LOCK:
            if not self.reconnecting:
                check_connection(self.token_path, self.connection)
            try:
                response = super().request(*args, **kwargs)
            except RefreshError as error:
                if error.retryable:
                    raise
                block_connection(self.token_path, self.connection, "token_refresh_rejected")
                raise ReconnectRequired(reconnect_message(self.connection)) from None
            if authentication_response(response):
                block_connection(self.token_path, self.connection, "api_authentication_rejected")
                raise ReconnectRequired(reconnect_message(self.connection))
            return response
