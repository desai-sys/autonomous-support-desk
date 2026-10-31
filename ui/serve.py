"""
Container entrypoint: write Streamlit's OIDC settings from the environment,
then start the console.

Environment (set by infra/app.yaml):
    OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, OIDC_METADATA_URL, OIDC_REDIRECT_URI,
    OIDC_COOKIE_SECRET
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_secrets() -> None:
    if os.environ.get("AUTH_MODE", "local") != "oidc":
        return
    keys = {
        "redirect_uri": "OIDC_REDIRECT_URI",
        "cookie_secret": "OIDC_COOKIE_SECRET",
        "client_id": "OIDC_CLIENT_ID",
        "client_secret": "OIDC_CLIENT_SECRET",
        "server_metadata_url": "OIDC_METADATA_URL",
    }
    missing = [env for env in keys.values() if not os.environ.get(env)]
    if missing:
        sys.exit(f"AUTH_MODE=oidc but these variables are missing: {', '.join(missing)}")
    lines = ["[auth]"] + [f"{k} = {_toml_str(os.environ[env])}" for k, env in keys.items()]
    lines.append('client_kwargs = { scope = "openid email profile" }')
    secrets = ROOT / ".streamlit" / "secrets.toml"
    secrets.parent.mkdir(exist_ok=True)
    secrets.write_text("\n".join(lines) + "\n")
    secrets.chmod(0o600)


def main() -> None:
    write_secrets()
    os.chdir(ROOT)
    port = os.environ.get("PORT", "8501")
    os.execvp(sys.executable, [
        sys.executable, "-m", "streamlit", "run", "ui/app.py",
        "--server.port", port, "--server.address", "0.0.0.0",
        "--server.headless", "true", "--browser.gatherUsageStats", "false",
    ])


if __name__ == "__main__":
    main()
