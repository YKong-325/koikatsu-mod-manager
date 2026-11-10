"""Build the original stacked-card K mark and Windows icon."""
from pathlib import Path

from PIL import Image, ImageDraw


def build():
    destination = Path(__file__).resolve().parent.parent / "assets"
    destination.mkdir(exist_ok=True)
    scale = 8
    image = Image.new("RGBA", (128 * scale, 128 * scale))
    draw = ImageDraw.Draw(image)

    def polygon(points, color):
        draw.polygon([(x * scale, y * scale) for x, y in points], fill=color)

    polygon([(8, 25), (84, 9), (108, 103), (32, 119)], "#d3edf1")
    polygon([(26, 8), (94, 8), (120, 34), (120, 107),
             (112, 115), (26, 115), (18, 107), (18, 16)], "#087e94")
    polygon([(94, 8), (120, 34), (94, 34)], "#58c7d1")
    # A broad geometric K remains legible in the small Windows icon sizes.
    polygon([(38, 34), (51, 34), (51, 58), (74, 34), (91, 34),
             (64, 64), (93, 94), (75, 94), (51, 70),
             (51, 94), (38, 94)], "#ffffff")
    polygon([(101, 87), (116, 72), (127, 83), (112, 98)], "#e66b96")
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(destination / "app-mark.png")
    image.save(destination / "app.ico", sizes=[(16, 16), (24, 24), (32, 32),
                                             (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Generated assets/app-mark.png and assets/app.ico")


if __name__ == "__main__":
    build()
