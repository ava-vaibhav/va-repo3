#!/usr/bin/env python3
"""
Generate a Versori JWT signed with your organisation PKCS #8 private key.

Required configuration:
- VERSORI_SIGNING_KEY_ID
- VERSORI_EXTERNAL_USER_ID
- one of:
  - VERSORI_PRIVATE_KEY
  - VERSORI_PRIVATE_KEY_FILE

Optional configuration:
- VERSORI_TOKEN_LIFETIME_SECONDS (default: 3600)

Examples:
  python generate_jwt.py
  python generate_jwt.py --signing-key-id abc123 --external-user-id user-456
  python generate_jwt.py --verbose
"""

from __future__ import annotations

import argparse
import os
import sys
import time

try:
    import jwt
except ImportError as exc:
    raise SystemExit(
        "PyJWT is required. Install it with: pip install PyJWT[crypto]"
    ) from exc


def read_private_key() -> str:
    """Load the PKCS #8 PEM private key from env or file."""
    inline_key = os.getenv("VERSORI_PRIVATE_KEY")
    if inline_key:
        return inline_key

    key_file = os.getenv("VERSORI_PRIVATE_KEY_FILE")
    if key_file:
        with open(key_file, "r", encoding="utf-8") as handle:
            return handle.read()

    raise ValueError(
        "Set VERSORI_PRIVATE_KEY or VERSORI_PRIVATE_KEY_FILE with your PKCS #8 PEM key."
    )


def read_required_value(cli_value: str | None, env_name: str) -> str:
    value = cli_value or os.getenv(env_name)
    if not value:
        raise ValueError(f"Missing required value: {env_name}")
    return value


def sign_versori_jwt(
    private_key: str,
    signing_key_id: str,
    external_user_id: str,
    lifetime_seconds: int = 3600,
) -> str:
    """
    Create a JWT for a Versori end user.

    Versori expects:
    - iss = https://versori.com/sk/<signingKeyId>
    - sub = <external user id>
    - iat = current unix time
    - exp = iat + short lifetime
    """
    issued_at = int(time.time())
    payload = {
        "iss": f"https://versori.com/sk/{signing_key_id}",
        "sub": external_user_id,
        "iat": issued_at,
        "exp": issued_at + lifetime_seconds,
    }

    token = jwt.encode(payload, private_key, algorithm="RS256")
    return token if isinstance(token, str) else token.decode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a Versori JWT and print it to stdout."
    )
    parser.add_argument(
        "--signing-key-id",
        help="Versori signing key id. Falls back to VERSORI_SIGNING_KEY_ID.",
    )
    parser.add_argument(
        "--external-user-id",
        help="External user id for the JWT sub claim. Falls back to VERSORI_EXTERNAL_USER_ID.",
    )
    parser.add_argument(
        "--lifetime-seconds",
        type=int,
        default=int(os.getenv("VERSORI_TOKEN_LIFETIME_SECONDS", "3600")),
        help="JWT lifetime in seconds. Defaults to 3600.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print token metadata to stderr before the token.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        private_key = read_private_key()
        signing_key_id = read_required_value(
            args.signing_key_id, "VERSORI_SIGNING_KEY_ID"
        )
        external_user_id = read_required_value(
            args.external_user_id, "VERSORI_EXTERNAL_USER_ID"
        )

        token = sign_versori_jwt(
            private_key=private_key,
            signing_key_id=signing_key_id,
            external_user_id=external_user_id,
            lifetime_seconds=args.lifetime_seconds,
        )

        if args.verbose:
            print("Versori JWT generated successfully.", file=sys.stderr)
            print(f"issuer: https://versori.com/sk/{signing_key_id}", file=sys.stderr)
            print(f"subject: {external_user_id}", file=sys.stderr)
            print(f"token_lifetime_seconds: {args.lifetime_seconds}", file=sys.stderr)

        print(token)
        return 0

    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
