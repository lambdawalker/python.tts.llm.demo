"""Ordered PCM assembly; no model-generated shell commands or codecs."""

from pathlib import Path

import numpy as np
import soundfile as sf


def assemble(paths: list[Path], output: Path, pause_ms: int, max_seconds: int):
    if not paths:
        raise ValueError("No clips to assemble")
    infos = [sf.info(p) for p in paths]
    rate, channels = infos[0].samplerate, infos[0].channels
    if any(i.samplerate != rate or i.channels != channels for i in infos):
        raise ValueError("Clips must have the same sample rate and channel count")
    duration = sum(i.duration for i in infos) + pause_ms / 1000 * (len(paths) - 1)
    if duration > max_seconds:
        raise ValueError("Combined audio exceeds configured duration limit")
    temporary = output.with_suffix(".partial.wav")
    try:
        with sf.SoundFile(
            temporary, "w", samplerate=rate, channels=channels, format="WAV", subtype="PCM_16"
        ) as target:
            for index, path in enumerate(paths):
                if index:
                    target.write(np.zeros((round(rate * pause_ms / 1000), channels)))
                with sf.SoundFile(path) as source:
                    for block in source.blocks(blocksize=65536, dtype="float32", always_2d=True):
                        target.write(block)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
