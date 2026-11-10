"""Checks the mesh renderer's framing and the manifest merge, without a real mod."""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ModCatalogServer as server  # noqa: E402
import ModMeshRender as render  # noqa: E402

builder = server.load_module("catalog_builder", "Build-ModCatalog.py")


def quad():
    """One textured square facing the camera."""
    verts = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], dtype=np.float32)
    normals = np.tile(np.array([[0, 0, 1]], dtype=np.float32), (4, 1))
    uvs = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    indices = np.array([0, 1, 2, 0, 2, 3], dtype=np.int32)
    return (verts, normals, uvs, indices)


def solid_texture(colour):
    texture = type("Texture", (), {})()
    image = Image.new("RGBA", (8, 8), colour)
    texture.image = image
    return texture


def check_render():
    parts = [(quad(), 0, 6, solid_texture((220, 40, 40, 255)), "quad")]
    image = render.render(parts, size=96, supersample=2)
    assert image is not None, "a textured quad must render"
    assert image.size == (96, 96)
    pixels = np.asarray(image)
    mask = pixels[..., 3] > 24
    coverage = mask.sum() / (96 * 96)
    assert 0.55 < coverage < 0.92, f"the square should nearly fill the frame, got {coverage:.2f}"
    covered = pixels[mask]
    assert covered[:, 0].mean() > 90, "the texture colour should survive"
    assert pixels[0, 0, 3] == 0, "the corners stay transparent"
    print(f"  render: {coverage:.0%} of the frame covered, corners transparent")


def check_empty():
    assert render.render([]) is None, "no geometry means no picture"
    print("  render: empty geometry returns None")


def check_trim():
    canvas = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
    block = Image.new("RGBA", (120, 120), (200, 200, 200, 255))
    canvas.paste(block, (20, 20))
    canvas.paste(Image.new("RGBA", (6, 6), (200, 200, 200, 255)), (170, 180))
    box = render._content_box(canvas)
    assert box is not None
    left, top, right, bottom = box
    assert right - left <= 130 and bottom - top <= 130, f"the speck must not widen the box: {box}"
    assert left >= 15 and top >= 15, f"the block's own margin stays: {box}"
    print("  framing: a stray speck is trimmed, the item keeps its box")


def check_merge():
    records = [
        {"path": "a.zipmod", "images": []},
        {"path": "b.zipmod", "images": [{"src": "thumbs/real.webp", "source": "file"}]},
        {"path": "c.zipmod", "images": []},
    ]
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder)
        (out / "thumbs").mkdir()
        (out / "thumbs" / "drawn.webp").write_bytes(b"x")
        (out / "renders.json").write_text(json.dumps({
            "a.zipmod": {"src": "thumbs/drawn.webp", "name": "网格渲染预览"},
            "b.zipmod": {"src": "thumbs/drawn.webp", "name": "网格渲染预览"},
            "c.zipmod": {"src": "thumbs/gone.webp", "name": "网格渲染预览"},
        }), encoding="utf-8")
        builder.merge_renders(records, out)
        assert [image["source"] for image in records[0]["images"]] == ["mesh-render"], \
            "a package without a picture gets the render"
        assert records[1]["images"] == [{"src": "thumbs/real.webp", "source": "file"}], \
            "a real preview is never replaced"
        assert records[2]["images"] == [], "a render whose file is gone is ignored"
        builder.merge_renders(records, out)
        assert len(records[0]["images"]) == 1, "merging twice does not duplicate the render"
    print("  manifest: renders fill the gap only, once, and only when the file exists")


check_render()
check_empty()
check_trim()
check_merge()
print("PASS: mesh render framing, empty geometry, stray-speck trimming, manifest merge")
