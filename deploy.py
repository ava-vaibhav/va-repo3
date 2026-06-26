#!/usr/bin/env python3
"""
Install the Versori CLI, authenticate via JWT context, and deploy project files.

CI/CD friendly:
- secrets from environment variables or a private key file
- JWT signed with organisation PKCS #8 private key for `versori context add`
- deploy via `versori projects deploy` (no direct API / tarball upload)

Required configuration:
- VERSORI_SIGNING_KEY_ID
- VERSORI_EXTERNAL_USER_ID
- one of VERSORI_PRIVATE_KEY or VERSORI_PRIVATE_KEY_FILE
- VERSORI_ORG_ID
- VERSORI_PROJECT_ENV
- VERSORI_PROJECT_ID (or .versori in deploy directory with project_id)

Optional configuration:
- VERSORI_DEPLOY_DIRECTORY (empty or "." = repo root; else subfolder name)
- DEPLOY_BRANCH / GITHUB_REF_NAME
- GITHUB_SHA
- VERSORI_VERSION_NAME
- VERSORI_DEPLOY_DESCRIPTION
- VERSORI_TOKEN_LIFETIME_SECONDS (default: 3600)
- VERSORI_CLI_VERSION (pin CLI release tag)
- VERSORI_CLI_INSTALL_DIR (default: .versori-cli/bin)
- VERSORI_DEPLOY_ASSETS (true/false)
- VERSORI_DEBUG (true/false; verbose logging with --debug or --dry-run)

CI/tooling files at repo root are excluded from deploy via .gitignore
(versori projects deploy respects .gitignore patterns).

Examples:
  python deploy.py --branch cicd-test --dry-run
  python deploy.py --branch cicd-test --debug
  python deploy.py --branch cicd-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

try:
    import jwt
except ImportError as exc:
    raise SystemExit(
        "PyJWT is required. Install it with: pip install PyJWT[crypto]"
    ) from exc


VERSORI_CLI_REPO = "versori/cli"
DEFAULT_CLI_INSTALL_DIR = ".versori-cli/bin"
CONTEXT_NAME = "ci"


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


def read_branch(cli_value: str | None) -> str:
    value = (
        cli_value
        or os.getenv("DEPLOY_BRANCH")
        or os.getenv("GITHUB_REF_NAME")
        or os.getenv("GITHUB_HEAD_REF")
    )
    if not value:
        raise ValueError(
            "Missing branch. Pass --branch or set DEPLOY_BRANCH / GITHUB_REF_NAME."
        )
    return value


def read_commit_sha() -> str:
    return os.getenv("GITHUB_SHA", "")


def mask_token(token: str) -> str:
    if len(token) <= 12:
        return "***"
    return f"{token[:8]}...{token[-4:]}"


def is_debug_mode(args: argparse.Namespace) -> bool:
    """Enable verbose pipeline logging for local testing, dry-run, or explicit debug."""
    return (
        getattr(args, "debug", False)
        or parse_bool(os.getenv("VERSORI_DEBUG"))
        or args.dry_run
    )


def log_pipeline_step(step: int, total: int, message: str) -> None:
    print(f"[pipeline {step}/{total}] {message}")


def log_cli_command(argv: list[str]) -> None:
    printable = " ".join(shlex.quote(part) for part in argv)
    print(f"cli_command: {printable}")


def run_cli(
    argv: list[str],
    *,
    input_text: str | None = None,
    debug: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a CLI command with pre-execution logging and captured output."""
    log_cli_command(argv)
    if input_text is not None:
        if debug:
            print(f"cli_stdin: {input_text}")
        else:
            print(f"cli_stdin: <redacted, length={len(input_text)}>")

    result = subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    print(f"cli_exit_code: {result.returncode}")
    if result.stdout:
        print(f"cli_stdout:\n{result.stdout.rstrip()}")
    if result.stderr:
        print(f"cli_stderr:\n{result.stderr.rstrip()}", file=sys.stderr)
    return result


def parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if not value:
        return False
    return value.strip().lower() in {"true", "1", "yes", "on"}


def sign_versori_jwt(
    private_key: str,
    signing_key_id: str,
    external_user_id: str,
    lifetime_seconds: int = 3600,
) -> str:
    """Create a JWT for Versori CLI context authentication."""
    issued_at = int(time.time())
    payload = {
        "iss": f"https://versori.com/sk/{signing_key_id}",
        "sub": external_user_id,
        "iat": issued_at,
        "exp": issued_at + lifetime_seconds,
    }

    token = jwt.encode(payload, private_key, algorithm="RS256")
    return token if isinstance(token, str) else token.decode("utf-8")


def detect_cli_platform() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        os_name = "linux"
    elif system == "darwin":
        os_name = "darwin"
    else:
        raise ValueError(f"Unsupported OS for Versori CLI install: {platform.system()}")

    if machine in {"x86_64", "amd64"}:
        arch = "amd64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise ValueError(f"Unsupported architecture for Versori CLI install: {machine}")

    return os_name, arch


def fetch_latest_cli_version() -> str:
    url = f"https://api.github.com/repos/{VERSORI_CLI_REPO}/releases/latest"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    version = payload.get("tag_name")
    if not version:
        raise ValueError("Could not determine latest Versori CLI release version.")
    return version


def verify_sha256(file_path: Path, expected: str) -> None:
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError(
            f"Checksum mismatch for {file_path.name}: expected {expected}, got {digest}"
        )


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        destination.write_bytes(response.read())


def ensure_versori_cli(cli_version: str | None = None, *, debug: bool = False) -> Path:
    """Install Versori CLI to a writable directory if not already on PATH."""
    existing = shutil.which("versori")
    if existing:
        version_argv = ["versori", "version"]
        result = run_cli(version_argv, debug=debug)
        if result.returncode == 0:
            print(f"versori_cli: {existing} (already on PATH)")
            return Path(existing)

    install_dir = Path(
        os.getenv("VERSORI_CLI_INSTALL_DIR", DEFAULT_CLI_INSTALL_DIR)
    ).resolve()
    install_dir.mkdir(parents=True, exist_ok=True)
    binary_path = install_dir / "versori"

    version = cli_version or os.getenv("VERSORI_CLI_VERSION") or fetch_latest_cli_version()
    version_num = version.lstrip("v")
    os_name, arch = detect_cli_platform()
    archive_name = f"cli_{version_num}_{os_name}_{arch}.tar.gz"
    archive_url = (
        f"https://github.com/{VERSORI_CLI_REPO}/releases/download/{version}/{archive_name}"
    )
    checksums_url = (
        f"https://github.com/{VERSORI_CLI_REPO}/releases/download/{version}/checksums.txt"
    )

    print(f"versori_cli_install: {version} ({os_name}/{arch})")
    print(f"versori_cli_url: {archive_url}")
    print(f"versori_cli_checksums_url: {checksums_url}")

    with tempfile.TemporaryDirectory(prefix="versori-cli-") as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / archive_name
        checksums_path = tmp_path / "checksums.txt"

        print(f"cli_download: {archive_url} -> {archive_path}")
        download_file(archive_url, archive_path)
        print(f"cli_download: {checksums_url} -> {checksums_path}")
        download_file(checksums_url, checksums_path)

        expected_sum = ""
        for line in checksums_path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == archive_name:
                expected_sum = parts[0]
                break
        if not expected_sum:
            raise ValueError(f"Checksum not found for {archive_name}")

        verify_sha256(archive_path, expected_sum)

        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extract("versori", path=tmp_path)

        extracted = tmp_path / "versori"
        shutil.copy2(extracted, binary_path)
        binary_path.chmod(0o755)

    os.environ["PATH"] = f"{install_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    print(f"versori_cli: {binary_path}")
    return binary_path


def setup_versori_context(
    versori_bin: Path, org_id: str, jwt_token: str, *, debug: bool = False
) -> Path:
    """Create ephemeral CLI config and add JWT context."""
    config_fd, config_path_str = tempfile.mkstemp(prefix="versori-config-", suffix=".yaml")
    os.close(config_fd)
    config_path = Path(config_path_str)

    context_argv = [
        str(versori_bin),
        "--config",
        str(config_path),
        "context",
        "add",
        "--name",
        CONTEXT_NAME,
        "--organisation",
        org_id,
        "--jwt",
        "-",
    ]
    result = run_cli(context_argv, input_text=jwt_token, debug=debug)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"versori context add failed: {stderr}")

    print(f"versori_context: {CONTEXT_NAME}")
    print(f"versori_config: {config_path}")
    return config_path


def read_versori_file(directory: Path) -> dict[str, str] | None:
    versori_path = directory / ".versori"
    if not versori_path.is_file():
        return None

    try:
        data = json.loads(versori_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid .versori file at {versori_path}: {exc}") from exc

    project_id = data.get("project_id")
    if not project_id:
        raise ValueError(f".versori at {versori_path} is missing project_id")

    return {"project_id": str(project_id), "context": str(data.get("context", ""))}


def resolve_project_id(deploy_dir: Path, cli_project_id: str | None) -> str:
    """Resolve project ID: explicit env/CLI wins, else .versori in deploy directory."""
    env_project_id = cli_project_id or os.getenv("VERSORI_PROJECT_ID")
    versori_file = read_versori_file(deploy_dir)
    file_project_id = versori_file["project_id"] if versori_file else None

    if env_project_id:
        if file_project_id and env_project_id != file_project_id:
            print(
                f"warning: VERSORI_PROJECT_ID / --project-id {env_project_id!r} "
                f"overrides .versori project {file_project_id!r} (in {deploy_dir})"
            )
        return env_project_id

    if file_project_id:
        print(f"project_id_source: .versori ({deploy_dir / '.versori'})")
        return file_project_id

    raise ValueError(
        "Missing project ID. Set VERSORI_PROJECT_ID / --project-id or add "
        f"a .versori file in {deploy_dir}"
    )


def resolve_deploy_directory(cli_value: str | None) -> Path:
    """Normalize VERSORI_DEPLOY_DIRECTORY: empty or '.' = repo root."""
    raw = cli_value if cli_value is not None else os.getenv("VERSORI_DEPLOY_DIRECTORY", ".")
    raw = raw.strip()
    if not raw or raw == ".":
        deploy_dir = Path.cwd()
    elif Path(raw).is_absolute():
        deploy_dir = Path(raw)
    else:
        deploy_dir = Path.cwd() / raw

    deploy_dir = deploy_dir.resolve()
    if not deploy_dir.is_dir():
        raise ValueError(f"Deploy directory does not exist: {deploy_dir}")

    return deploy_dir


def build_deploy_version_name(branch: str, commit_sha: str, cli_version: str | None) -> str:
    version_name = cli_version or os.getenv("VERSORI_VERSION_NAME")
    if version_name:
        return version_name
    short_sha = commit_sha[:7] if commit_sha else "unknown"
    return f"{branch}-{short_sha}"


def build_deploy_description(branch: str, commit_sha: str, cli_description: str | None) -> str:
    if cli_description:
        return cli_description
    env_description = os.getenv("VERSORI_DEPLOY_DESCRIPTION")
    if env_description:
        return env_description
    return f"branch={branch} commit={commit_sha or 'unknown'}"


def build_versori_deploy_argv(
    versori_bin: Path,
    config_path: Path,
    *,
    project_id: str,
    environment: str,
    directory: Path,
    version: str,
    description: str,
    dry_run: bool,
    upload_assets: bool,
) -> list[str]:
    argv = [
        str(versori_bin),
        "--config",
        str(config_path),
        "projects",
        "deploy",
        "--project",
        project_id,
        "--environment",
        environment,
        "--directory",
        str(directory),
        "--version",
        version,
        "--description",
        description,
    ]
    if dry_run:
        argv.append("--dry-run")
    if upload_assets:
        argv.append("--assets")
    return argv


def run_versori_deploy(argv: list[str], *, debug: bool = False) -> int:
    result = run_cli(argv, debug=debug)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install Versori CLI, authenticate, and deploy project files."
    )
    parser.add_argument(
        "--signing-key-id",
        help="Versori signing key id. Falls back to VERSORI_SIGNING_KEY_ID.",
    )
    parser.add_argument(
        "--external-user-id",
        help="External user id for JWT sub claim. Falls back to VERSORI_EXTERNAL_USER_ID.",
    )
    parser.add_argument(
        "--branch",
        help="Git branch being deployed. Falls back to DEPLOY_BRANCH or GITHUB_REF_NAME.",
    )
    parser.add_argument(
        "--project-id",
        help="Versori project ID. Falls back to VERSORI_PROJECT_ID or .versori.",
    )
    parser.add_argument(
        "--environment",
        help="Versori environment name. Falls back to VERSORI_PROJECT_ENV.",
    )
    parser.add_argument(
        "--directory",
        help=(
            "Directory to deploy. Falls back to VERSORI_DEPLOY_DIRECTORY "
            "(empty or '.' = repo root)."
        ),
    )
    parser.add_argument(
        "--version",
        help="Deploy version name. Falls back to VERSORI_VERSION_NAME or branch-sha.",
    )
    parser.add_argument(
        "--description",
        help="Deploy version description. Falls back to VERSORI_DEPLOY_DESCRIPTION.",
    )
    parser.add_argument(
        "--cli-version",
        help="Versori CLI release tag to install. Falls back to VERSORI_CLI_VERSION.",
    )
    parser.add_argument(
        "--lifetime-seconds",
        type=int,
        default=int(os.getenv("VERSORI_TOKEN_LIFETIME_SECONDS", "3600")),
        help="JWT lifetime in seconds. Defaults to 3600.",
    )
    parser.add_argument(
        "--assets",
        action="store_true",
        help="Also upload assets from versori-research. Also enabled when VERSORI_DEPLOY_ASSETS=true.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to versori projects deploy (list files, no upload).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Verbose pipeline logging: full JWT, CLI commands, and step progress. "
            "Also enabled when VERSORI_DEBUG=true or --dry-run is set."
        ),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config_path: Path | None = None
    pipeline_steps = 7

    try:
        debug_mode = is_debug_mode(args)
        in_ci = os.getenv("GITHUB_ACTIONS") == "true"

        log_pipeline_step(1, pipeline_steps, "Resolving deployment context")
        branch = read_branch(args.branch)
        commit_sha = read_commit_sha()
        deploy_dir = resolve_deploy_directory(args.directory)

        print("=== Deployment context ===")
        print(f"branch: {branch}")
        print(f"deploy_directory: {deploy_dir}")
        print(f"commit_sha: {commit_sha or '(not set)'}")
        print(f"timestamp_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
        print(f"ci_run: {in_ci}")
        print(f"debug_mode: {debug_mode}")

        log_pipeline_step(2, pipeline_steps, "Loading credentials and configuration")
        private_key = read_private_key()
        signing_key_id = read_required_value(
            args.signing_key_id, "VERSORI_SIGNING_KEY_ID"
        )
        external_user_id = read_required_value(
            args.external_user_id, "VERSORI_EXTERNAL_USER_ID"
        )
        org_id = read_required_value(None, "VERSORI_ORG_ID")
        environment = read_required_value(args.environment, "VERSORI_PROJECT_ENV")
        project_id = resolve_project_id(deploy_dir, args.project_id)
        version = build_deploy_version_name(branch, commit_sha, args.version)
        description = build_deploy_description(branch, commit_sha, args.description)
        upload_assets = args.assets or parse_bool(os.getenv("VERSORI_DEPLOY_ASSETS"))
        print(f"org_id: {org_id}")
        print(f"signing_key_id: {signing_key_id}")
        print(f"external_user_id: {external_user_id}")

        log_pipeline_step(3, pipeline_steps, "Generating Versori JWT")
        token = sign_versori_jwt(
            private_key=private_key,
            signing_key_id=signing_key_id,
            external_user_id=external_user_id,
            lifetime_seconds=args.lifetime_seconds,
        )

        print("Versori JWT generated successfully.")
        print(f"issuer: https://versori.com/sk/{signing_key_id}")
        print(f"subject: {external_user_id}")
        print(f"token_lifetime_seconds: {args.lifetime_seconds}")
        if debug_mode or not in_ci:
            print(f"jwt_token: {token}")
        else:
            print(f"jwt_token: {mask_token(token)}")
        print(f"project_id: {project_id}")
        print(f"environment: {environment}")
        print(f"version: {version}")
        print(f"description: {description}")
        print(f"upload_assets: {upload_assets}")
        print(f"dry_run: {args.dry_run}")

        log_pipeline_step(4, pipeline_steps, "Installing or locating Versori CLI")
        versori_bin = ensure_versori_cli(args.cli_version, debug=debug_mode)

        log_pipeline_step(5, pipeline_steps, "Configuring Versori CLI context (JWT auth)")
        config_path = setup_versori_context(versori_bin, org_id, token, debug=debug_mode)

        log_pipeline_step(6, pipeline_steps, "Building deploy command")
        deploy_argv = build_versori_deploy_argv(
            versori_bin,
            config_path,
            project_id=project_id,
            environment=environment,
            directory=deploy_dir,
            version=version,
            description=description,
            dry_run=args.dry_run,
            upload_assets=upload_assets,
        )
        print(f"deploy_argv_ready: {len(deploy_argv)} arguments")

        log_pipeline_step(7, pipeline_steps, "Executing versori projects deploy")
        if args.dry_run:
            print("Dry run enabled. versori projects deploy --dry-run will list files only.")

        exit_code = run_versori_deploy(deploy_argv, debug=debug_mode)
        if exit_code != 0:
            print(f"versori_exit_code: {exit_code}", file=sys.stderr)
            log_pipeline_step(7, pipeline_steps, f"Deploy failed (exit_code={exit_code})")
        else:
            log_pipeline_step(7, pipeline_steps, "Deploy completed successfully")
        return 0 if exit_code == 0 else 1

    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        if config_path and config_path.is_file():
            config_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
