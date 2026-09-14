"""Networked integration check for the pinned optional model and labels."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from imagesorter.ai_tagger import AITagger, ModelDownloader


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args(argv)
    results: list[tuple[bool, str]] = []
    downloader = ModelDownloader(str(args.model_dir))
    downloader.finished.connect(lambda ok, message: results.append((ok, message)))
    downloader.run()
    if not results or not results[-1][0]:
        detail = results[-1][1] if results else "downloader emitted no result"
        print(f"Optional AI download failed: {detail}", file=sys.stderr)
        return 1

    args.fixture.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (224, 224), color=(120, 80, 40)).save(args.fixture, "JPEG")
    tagger = AITagger(str(args.model_dir), hardware_acceleration=False)
    tags = tagger.get_tags(str(args.fixture), threshold=0.0)
    if not tags or tagger.active_provider != "CPUExecutionProvider":
        print(
            f"Optional AI inference failed: provider={tagger.active_provider}, tags={tags}",
            file=sys.stderr,
        )
        return 1
    print(f"provider={tagger.active_provider}; tags={tags}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
