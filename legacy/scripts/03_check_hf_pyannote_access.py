#!/usr/bin/env python3
"""Check local Hugging Face authentication for pyannote diarization models."""

from __future__ import annotations

import sys

from huggingface_hub import HfApi, HfFolder
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

from env_utils import load_dotenv


REPOS = {
    "pyannote/speaker-diarization-community-1": {
        "filename": "config.yaml",
        "required": True,
    },
    "pyannote/segmentation-3.0": {
        "filename": "config.yaml",
        "required": False,
    },
}


def main() -> int:
    load_dotenv()
    token = HfFolder.get_token()
    if not token:
        print("No local Hugging Face token found.")
        print("Run: uv run --group audio hf auth login")
        print("Or set: export HF_TOKEN=...")
        return 2

    api = HfApi(token=token)
    try:
        user = api.whoami().get("name", "unknown")
    except HfHubHTTPError as exc:
        print(f"Hugging Face token is present but not valid: {exc}", file=sys.stderr)
        return 2

    print(f"Authenticated as: {user}")
    failed = False
    for repo, config in REPOS.items():
        filename = config["filename"]
        required = config["required"]
        try:
            info = api.model_info(repo)
            hf_hub_download(repo_id=repo, filename=filename, token=token)
            label = "required" if required else "optional"
            print(f"OK: {repo} ({label}) gated={getattr(info, 'gated', None)} file={filename}")
        except GatedRepoError:
            label = "required" if required else "optional"
            print(f"NO ACCESS: {repo} ({label})")
            if required:
                failed = True
        except HfHubHTTPError as exc:
            print(f"ERROR: {repo}: {exc}", file=sys.stderr)
            if required:
                failed = True

    if failed:
        print("\nOpen required pages in a browser while logged in and accept/request access:")
        for repo, config in REPOS.items():
            if config["required"]:
                print(f"https://huggingface.co/{repo}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
