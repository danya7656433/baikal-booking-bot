"""Check rendered screenshots for nonblank, varied hero image pixels."""

from pathlib import Path

from PIL import Image, ImageStat

root = Path(__file__).resolve().parents[1] / "test-results"
for width in (320, 390, 844, 1440, 1920):
    path = root / f"landing-{width}.png"
    with Image.open(path) as screenshot:
        # Sample the lake below the title, excluding navigation and the next section.
        w, h = screenshot.size
        sample = screenshot.convert("RGB").crop((int(w * .2), int(h * .62), int(w * .8), int(h * .79)))
        deviation = ImageStat.Stat(sample).stddev
        colors = len(sample.getcolors(sample.width * sample.height))
        assert max(deviation) > 3, (width, deviation)
        assert colors > 100, (width, colors)
        print(f"{width}px: {colors} colors; channel deviation {max(deviation):.1f}")
