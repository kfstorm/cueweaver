"""Deployment-owned PySubtrans authentication and local administration.

Run ``cueweaver-chatgpt --help`` for the bootstrap workflow.
No OAuth management operations are exposed by the unauthenticated product API.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import uuid
from collections.abc import Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any, Literal

from PySubtrans.Auth.ChatGPTAuth import (
    PLAN_USAGE_SCOPE,
    ChatGPTAuth,
    ChatGPTAuthError,
    ConfigureChatGPTApp,
)
from PySubtrans.Helpers.Resources import ConfigureConfigDir

from .work import WorkRoot, WorkRootLease

SIGN_IN_HELP = (
    "See Settings > ChatGPT for administrator authentication instructions. "
    "Stop CueWeaver and run cueweaver-chatgpt --work-root "
    "<absolute-work-root> sign-in on the browser's machine, or use the "
    "remote bootstrap instructions in the README. Then restart CueWeaver."
)


def read_chatgpt_status(
    path: Path,
) -> Literal["credentials_present", "not_configured", "unavailable"]:
    """Read local presence only, without refreshing or changing credentials."""
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("Credential storage is a symbolic link")
        # Upstream Load suppresses read/parse errors. Read once ourselves so an
        # incomplete concurrent write is not mistaken for an unconfigured account.
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "not_configured"
    except (OSError, ValueError):
        return "unavailable"
    if not isinstance(data, dict):
        return "unavailable"
    credentials = data.get("credentials")
    if credentials is None:
        return "not_configured"
    if isinstance(credentials, dict):
        token = credentials.get("refresh_token")
        if isinstance(token, str) and token.strip():
            return "credentials_present"
    return "unavailable"


def configure_chatgpt(work_root: Path) -> ChatGPTAuth:
    """Configure the process once, while its Work-root lease is held."""
    root = WorkRoot(work_root)
    directory = root.path / "pysubtrans"
    path = directory / "chatgpt-auth.json"
    if directory.is_symlink() or path.is_symlink():
        raise ValueError("ChatGPT storage must not be a symbolic link")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    ConfigureConfigDir(str(directory))
    ConfigureChatGPTApp("CueWeaver", credential_filename=path.name)
    auth = ChatGPTAuth()
    # Do not silently replace a damaged identity or credential file with a new host.
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(
            "Invalid ChatGPT credential file; restore it or reset it offline"
        )
    if not data.get("host_id"):
        data["host_id"] = f"urn:uuid:{uuid.uuid4()}"
        auth.store.Save(data)
    path.chmod(0o600)
    return auth


def import_credentials(auth: ChatGPTAuth, source: Path) -> None:
    """Import a CueWeaver session without copying the browser machine's identity."""
    data = json.loads(source.read_text(encoding="utf-8"))
    credentials = data.get("credentials") if isinstance(data, dict) else None
    if (
        not isinstance(credentials, dict)
        or not isinstance(data.get("client_id"), str)
        or not data["client_id"]
        or any(
            not isinstance(credentials.get(key), str) or not credentials[key]
            for key in ("access_token", "refresh_token", "id_token", "scope")
        )
        or PLAN_USAGE_SCOPE not in credentials["scope"].split()
        or type(credentials.get("expires_at")) not in (int, float)
        or not math.isfinite(credentials["expires_at"])
    ):
        raise ValueError("Not a complete CueWeaver ChatGPT credential file")
    destination = auth.store.Load()
    if auth.is_signed_in:
        raise ValueError("Sign out of the destination account before importing")
    # A transferred session keeps its issued registration, but never its host ID.
    destination.update(client_id=data["client_id"], credentials=credentials)
    auth.store.Save(destination)


def require_chatgpt_session(settings: dict[str, Any]) -> None:
    """Fail before creating a project or a provider when reauthentication is needed."""
    if not settings.get("use_chatgpt_plan", False):
        return
    try:
        ChatGPTAuth().GetAccessToken(min_validity=1800.0)
    except ChatGPTAuthError:
        # OAuth responses are untrusted and can contain secrets. Never persist them
        # as Job errors or return them through the ordinary Model Profile API.
        raise ValueError("ChatGPT session unavailable. " + SIGN_IN_HELP) from None


def is_chatgpt_auth_failure(error: BaseException) -> bool:
    """Recognize typed OAuth errors and HTTP 401 through upstream wrappers."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            isinstance(current, ChatGPTAuthError)
            or getattr(current, "status_code", None) == HTTPStatus.UNAUTHORIZED
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-root", type=Path, default=os.getenv("CUEWEAVER_WORK_ROOT")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "sign-in", "status", "sign-out"):
        commands.add_parser(name)
    commands.add_parser("import").add_argument("source", type=Path)
    args = parser.parse_args(argv)
    if args.work_root is None:
        parser.error("--work-root or CUEWEAVER_WORK_ROOT is required")
    lease = WorkRootLease(args.work_root / ".cueweaver.lease")
    previous_logging_disable = logging.root.manager.disable
    try:
        root = WorkRoot(args.work_root)
        root.prepare()
        lease.acquire()
        auth = configure_chatgpt(root.path)
        # Upstream may log remote OAuth error bodies. Management output must not
        # reveal tokens, codes, or callback URLs when an operation fails.
        logging.disable(logging.CRITICAL)
        if args.command == "sign-in":
            auth.SignIn()
            print(
                "Signed in with ChatGPT. Stop using this session locally after transfer."
            )
        elif args.command == "import":
            import_credentials(auth, args.source)
            print("ChatGPT session imported; destination host identity preserved.")
        elif args.command == "sign-out":
            revoked = auth.SignOut()
            print("Local credentials removed.")
            if not revoked:
                print(
                    "Revocation unconfirmed. Revoke CueWeaver access in ChatGPT settings."
                )
                return 1
        else:
            print(
                "ChatGPT credentials present."
                if auth.is_signed_in
                else "Not signed in."
            )
        return 0
    except ChatGPTAuthError:
        print(
            "ChatGPT authentication failed. Check connectivity or sign in again.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError):
        print(
            "Cannot manage ChatGPT credentials. Stop CueWeaver and check the Work root and credential file.",
            file=sys.stderr,
        )
        return 1
    finally:
        logging.disable(previous_logging_disable)
        lease.release()
