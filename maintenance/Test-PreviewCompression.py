"""Checks that previews are stored small and that the migration is restart-safe."""
import importlib.util
import io
import json
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

APP = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load("preview_builder", APP / "Build-ModCatalog.py")
compress = load("preview_compress", APP / "maintenance" / "Compress-Previews.py")


def noisy(size, alpha=False):
    """A photo-like image: random colour defeats PNG, which is the case that costs GB."""
    channels = 4 if alpha else 3
    pixels = bytearray()
    seed = 12345
    for _ in range(size[0] * size[1]):
        seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
        pixels += bytes(((seed >> 8) & 0xFF, (seed >> 16) & 0xFF, (seed >> 4) & 0xFF))
        if alpha:
            pixels.append(255)
    return Image.frombytes("RGBA" if alpha else "RGB", size, bytes(pixels)).filter(
        ImageFilter.GaussianBlur(3))


def encoded(image):
    suffix, payload = builder.encode_preview(image)
    return suffix, len(payload)


def main():
    lossless = io.BytesIO()
    noisy((512, 512)).save(lossless, "PNG")
    suffix, size = encoded(noisy((512, 512)))
    assert suffix == ".webp", suffix
    assert size < len(lossless.getvalue()) / 4, (size, lossless.tell())

    """Alpha must survive, and the edge cap must still apply."""
    suffix, size = encoded(noisy((900, 900), alpha=True))
    assert suffix == ".webp" and size > 0
    with Image.open(io.BytesIO(builder.encode_preview(noisy((900, 900), alpha=True))[1])) as webp:
        assert webp.size == (512, 512), webp.size
        assert webp.mode in ("RGBA", "RGB"), webp.mode
        assert webp.convert("RGBA").getchannel("A").getextrema() == (255, 255)

    """A flat icon is cheaper as PNG, so it keeps PNG instead of bloating."""
    icon = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    ImageDraw.Draw(icon).rectangle([4, 4, 27, 27], fill=(220, 30, 30, 255))
    suffix, size = encoded(icon)
    reference = io.BytesIO()
    icon.save(reference, "PNG", optimize=True)
    assert suffix == ".png" and size == reference.tell(), (suffix, size, reference.tell())

    with tempfile.TemporaryDirectory(prefix="kk-compress-test-") as temporary:
        catalog = Path(temporary) / "catalog"
        (catalog / "thumbs").mkdir(parents=True)
        (catalog / "library_thumbs").mkdir()
        for name, image in (("aaaa", noisy((512, 512))), ("bbbb", noisy((256, 256), alpha=True))):
            image.save(catalog / "thumbs" / f"{name}.png")
        noisy((320, 320)).save(catalog / "library_thumbs" / "cccc.jpg", "JPEG", quality=84)
        (catalog / "index.json").write_text(json.dumps([{
            "path": "sample.zipmod",
            "images": [{"src": "thumbs/aaaa.png"}, {"src": "thumbs/bbbb.png"},
                       {"src": "thumbs/ghost.png"}],
        }]), "utf-8")
        (catalog / "library.json").write_text(json.dumps([
            {"path": "chara/a.png", "thumb": "library_thumbs/cccc.jpg"}]), "utf-8")

        before = sum(p.stat().st_size for p in (catalog / "thumbs").iterdir())
        report = compress.prepare(catalog, "thumbs", "thumbs_new", None, 2, False)
        assert report["swapped"] and report["files"] == 2, report
        records, changed, missing = compress.rewrite_mods(catalog)
        assert records == 1 and changed == 2 and missing == 1, (records, changed, missing)

        index = json.loads((catalog / "index.json").read_text("utf-8"))
        sources = [image["src"] for image in index[0]["images"]]
        assert sources == ["thumbs/aaaa.webp", "thumbs/bbbb.webp", "thumbs/ghost.png"], sources
        assert all((catalog / source).is_file() for source in sources[:2])
        after = sum(p.stat().st_size for p in (catalog / "thumbs").iterdir())
        assert after < before / 3, (before, after)
        assert (catalog / "_thumbs_old" / "aaaa.png").is_file(), "originals are retired, not lost"

        """Running twice must be a no-op, not a second re-encode."""
        assert not [p for p in (catalog / "thumbs").iterdir()
                    if p.suffix in {".png", ".jpg"}]
        assert compress.rewrite_mods(catalog)[1] == 0

        report = compress.prepare(catalog, "library_thumbs", "library_thumbs_new", None, 2, False)
        assert report["swapped"], report
        records, changed = compress.rewrite_library(catalog)
        assert records == 1 and changed == 1
        library = json.loads((catalog / "library.json").read_text("utf-8"))
        assert library[0]["thumb"] == "library_thumbs/cccc.webp", library

        freed = compress.purge(catalog, ["_thumbs_old", "_library_thumbs_old"])
        assert freed > 0 and not (catalog / "_thumbs_old").exists()

    print("PASS: WebP previews, alpha kept, edge cap, flat icons stay PNG, "
          "index rewrite, restart-safe rerun, retired originals, purge")


if __name__ == "__main__":
    main()
