"""record-features.mjs 가 남긴 frames/<장면>/ 를 docs/images/features/<장면>.gif 로 묶는다 (#1027).

    python -X utf8 frames-to-gif.py [장면 …]      # 빼면 frames/ 아래 전부

데스크톱은 가로 960, 모바일(가로 390 녹화)은 가로 360 으로 줄인다. 장마다 256색 팔레트를 따로 만들고
(디더링 없음 — 글자가 번지지 않게), Pillow 가 앞 장과 달라진 영역만 새로 쓴다. Pillow 가 필요하다(`pip install pillow`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
FRAMES = HERE / "frames"
OUT = HERE.parent / "images" / "features"


def to_gif(scene: Path) -> Path:
    meta = json.loads((scene / "frames.json").read_text(encoding="utf-8"))
    frames, durations = [], []
    for f in meta["frames"]:
        im = Image.open(scene / f["file"]).convert("RGB")
        width = 360 if im.width < 1000 else 960
        im = im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS)
        frames.append(im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
        durations.append(int(f["ms"]))
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{meta['name']}.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True, disposal=1)
    return out


def main(names: list[str]) -> None:
    scenes = [FRAMES / n for n in names] if names else sorted(p for p in FRAMES.iterdir() if (p / "frames.json").exists())
    for scene in scenes:
        out = to_gif(scene)
        print(f"{out.relative_to(HERE.parent.parent)}  {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1:])
