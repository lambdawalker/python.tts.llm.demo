"""uv run tts-llm-demo --config config.yaml"""

import argparse
from pathlib import Path

import uvicorn

from .app import create_app
from .config import load_config


def main():
    parser = argparse.ArgumentParser(description="Single-user TTS audio studio with remote vLLM")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        parser.exit(
            2, f"Configuration error: {exc}\nCopy config.example.yaml to config.yaml first.\n"
        )
    uvicorn.run(create_app(config), host=config.host, port=config.port, workers=1)


if __name__ == "__main__":
    main()
