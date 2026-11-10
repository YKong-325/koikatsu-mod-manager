"""Draw a preview image for a package that ships no thumbnail of its own.

The game's own list previews are textures the modder packed, so packages without
one have nothing to show. This renders the meshes the package already contains:
orthographic front view, texture-mapped triangles, painter-sorted depth. Only PIL
is used, because numpy is excluded from the packaged application.

Entry points:
    render_package(path, size) -> PIL.Image or None      # one archive
    python PreviewRenderer.py <zipmod> [<zipmod> ...]    # writes PNGs to a folder
"""
import io
import math
import zipfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance

SIZE = 320
CELLS = 4            # at most this many pieces share the frame
PAD = 0.94           # how much of a cell the drawing may fill
LIGHT = (0.4, 0.55, 0.73)
BACKGROUND = (255, 255, 255, 0)
MIN_AREA = 0.8       # skip triangles smaller than this many pixels
MAX_BUNDLE = 256 * 1024 * 1024


def _main_texture(material):
    props = getattr(material, "m_SavedProperties", None)
    if not props:
        return None
    for entry in props.m_TexEnvs:
        if entry[0] in ("_MainTex", "_BaseMap"):
            pointer = entry[1].m_Texture
            if pointer and pointer.path_id:
                return pointer
    return None


def _bounds(vertices):
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def _signature(vertices, materials):
    """Fingerprint that says "this piece would draw exactly like that one".

    Packages routinely ship the same mesh twice (a spare copy per coordinate
    type, or two objects the game swaps at runtime), and drawing both wastes
    half the frame on a duplicate. Vertices alone are not enough to merge on:
    two coincident pieces may still wear different textures, so the materials
    are part of the key.
    """
    step = max(1, len(vertices) // 24)
    sample = tuple(round(value, 4) for vertex in vertices[::step][:24] for value in vertex)
    left, top, right, bottom = _bounds(vertices)
    return (len(vertices), round(right - left, 4), round(bottom - top, 4), sample,
            tuple(getattr(pointer, "path_id", None) for pointer in materials))


def _collect(env):
    """One group per rendered mesh: vertices, normals, uvs, indices, materials."""
    import UnityPy  # noqa: F401  (kept close to use so import cost stays local)

    materials, meshes, textures = {}, {}, {}
    filters, renderers = {}, {}
    for obj in env.objects:
        kind = obj.type.name
        try:
            if kind == "Material":
                materials[obj.path_id] = obj.read()
            elif kind == "Mesh":
                meshes[obj.path_id] = obj.read()
            elif kind == "Texture2D":
                textures[obj.path_id] = obj.read()
            elif kind == "MeshFilter":
                item = obj.read()
                filters[getattr(getattr(item, "m_GameObject", None), "path_id", None)] = \
                    getattr(getattr(item, "m_Mesh", None), "path_id", None)
            elif kind in ("SkinnedMeshRenderer", "MeshRenderer"):
                item = obj.read()
                renderers[getattr(getattr(item, "m_GameObject", None), "path_id", None)] = item
        except Exception:
            continue

    from UnityPy.helpers.MeshHelper import MeshHandler

    groups = []
    seen = set()
    for owner, renderer in renderers.items():
        mesh_id = getattr(getattr(renderer, "m_Mesh", None), "path_id", None) or filters.get(owner)
        mesh = meshes.get(mesh_id)
        if mesh is None:
            continue
        try:
            handler = MeshHandler(mesh)
            handler.process()
        except Exception:
            continue
        vertices = handler.m_Vertices
        if not vertices:
            continue
        normals = handler.m_Normals if handler.m_Normals and len(handler.m_Normals) == len(vertices) else None
        uvs = handler.m_UV0 if handler.m_UV0 and len(handler.m_UV0) == len(vertices) else None
        indices = list(handler.m_IndexBuffer or [])
        indices = indices[:(len(indices) // 3) * 3]
        material_ptrs = list(getattr(renderer, "m_Materials", None) or [])
        submeshes = []
        for index, sub in enumerate(mesh.m_SubMeshes or []):
            width = 4 if getattr(mesh, "m_IndexFormat", 0) == 1 else 2
            material = materials.get(getattr(material_ptrs[index], "path_id", None)) \
                if index < len(material_ptrs) else None
            pointer = _main_texture(material) if material else None
            texture = textures.get(getattr(pointer, "path_id", None)) if pointer else None
            if texture is not None:
                try:
                    texture = texture.image.convert("RGBA")
                except Exception:
                    texture = None
            submeshes.append((sub.firstByte // width, sub.indexCount, texture))
        signature = _signature(vertices, material_ptrs)
        if signature in seen:
            continue
        seen.add(signature)
        groups.append((vertices, normals, uvs, indices, submeshes))
    return groups


def _affine(screen, texture_points, dx, dy):
    """PIL AFFINE maps destination to source, so solve the destination->texture fit."""
    (x0, y0), (x1, y1), (x2, y2) = screen
    (u0, v0), (u1, v1), (u2, v2) = texture_points
    det = (x0 - x2) * (y1 - y2) - (x1 - x2) * (y0 - y2)
    if abs(det) < 1e-9:
        return None
    a = ((u0 - u2) * (y1 - y2) - (u1 - u2) * (y0 - y2)) / det
    b = ((u1 - u2) * (x0 - x2) - (u0 - u2) * (x1 - x2)) / det
    d = ((v0 - v2) * (y1 - y2) - (v1 - v2) * (y0 - y2)) / det
    e = ((v1 - v2) * (x0 - x2) - (v0 - v2) * (x1 - x2)) / det
    c = u0 - a * x0 - b * y0
    f = v0 - d * x0 - e * y0
    return (a, b, c + a * dx + b * dy, d, e, f + d * dx + e * dy)


def _draw(canvas, group, box, cell):
    vertices, normals, uvs, indices, submeshes = group
    left, top, right, bottom = box
    span = max(right - left, bottom - top, 1e-6)
    scale = (cell * PAD) / span
    midx, midy = (left + right) / 2, (top + bottom) / 2
    screen = [((v[0] - midx) * scale + cell / 2, cell / 2 - (v[1] - midy) * scale, v[2])
              for v in vertices]

    triangles = []
    for first, count, texture in submeshes:
        chunk = indices[first:first + count]
        for k in range(0, len(chunk) - 2, 3):
            i0, i1, i2 = chunk[k], chunk[k + 1], chunk[k + 2]
            if max(i0, i1, i2) >= len(screen):
                continue
            a, b, c = screen[i0], screen[i1], screen[i2]
            area = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
            if abs(area) < MIN_AREA:
                continue
            triangles.append(((a[2] + b[2] + c[2]) / 3, i0, i1, i2, texture))
    triangles.sort(key=lambda row: row[0])

    for _, i0, i1, i2, texture in triangles:
        a, b, c = screen[i0], screen[i1], screen[i2]
        minx, maxx = int(min(a[0], b[0], c[0])), int(max(a[0], b[0], c[0])) + 1
        miny, maxy = int(min(a[1], b[1], c[1])), int(max(a[1], b[1], c[1])) + 1
        if maxx < 0 or maxy < 0 or minx >= cell or miny >= cell:
            continue
        width, height = max(1, maxx - minx), max(1, maxy - miny)
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).polygon(
            [(a[0] - minx, a[1] - miny), (b[0] - minx, b[1] - miny),
             (c[0] - minx, c[1] - miny)], fill=255)
        patch = None
        if texture is not None and uvs is not None:
            (u0, v0), (u1, v1), (u2, v2) = uvs[i0], uvs[i1], uvs[i2]
            tw, th = texture.size
            coefficients = _affine(
                [(a[0], a[1]), (b[0], b[1]), (c[0], c[1])],
                [(u0 * tw, (1 - v0) * th), (u1 * tw, (1 - v1) * th), (u2 * tw, (1 - v2) * th)],
                minx, miny)
            if coefficients is not None:
                try:
                    patch = texture.transform((width, height), Image.AFFINE, coefficients,
                                              resample=Image.NEAREST)
                except Exception:
                    patch = None
        if patch is None:
            patch = Image.new("RGBA", (width, height), (168, 168, 168, 255))
        if normals is not None:
            nx = normals[i0][0] + normals[i1][0] + normals[i2][0]
            ny = normals[i0][1] + normals[i1][1] + normals[i2][1]
            nz = normals[i0][2] + normals[i1][2] + normals[i2][2]
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            shade = 0.55 + 0.45 * abs((nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / length)
            source_alpha = patch.getchannel("A")
            patch = ImageEnhance.Brightness(patch.convert("RGB")) \
                .enhance(min(shade, 1.15)).convert("RGBA")
            patch.putalpha(source_alpha)
        alpha = ImageChops.multiply(patch.getchannel("A"), mask)
        canvas.paste(patch, (minx, miny), alpha)


def _weigh(group):
    vertices = group[0]
    left, top, right, bottom = _bounds(vertices)
    return (right - left) * (bottom - top)


def _grid(count):
    """Columns and rows for this many pieces: one piece owns the whole frame."""
    if count <= 1:
        return 1, 1
    if count == 2:
        return 2, 1
    return 2, 2


def render_package(path, size=SIZE):
    """Composite the largest pieces of one archive into a square preview."""
    import UnityPy

    try:
        with zipfile.ZipFile(path) as archive:
            bundle = next((info for info in archive.infolist()
                           if not info.is_dir()
                           and info.filename.lower().endswith((".unity3d", ".unity"))
                           and info.file_size < MAX_BUNDLE), None)
            if bundle is None:
                return None
            env = UnityPy.load(io.BytesIO(archive.read(bundle)))
    except Exception:
        return None
    try:
        groups = _collect(env)
    except Exception:
        return None
    if not groups:
        return None
    groups.sort(key=_weigh, reverse=True)
    groups = groups[:CELLS]
    columns, rows = _grid(len(groups))
    cell = size // max(columns, rows)
    # Centre the grid: two pieces used to sit in the top-left quarter of an
    # otherwise empty square, which reads as "tiny and off to one side".
    origin_x = (size - columns * cell) // 2
    origin_y = (size - rows * cell) // 2
    canvas = Image.new("RGBA", (size, size), BACKGROUND)
    for index, group in enumerate(groups):
        column, row = index % columns, index // columns
        tile = Image.new("RGBA", (cell, cell), BACKGROUND)
        _draw(tile, group, _bounds(group[0]), cell)
        canvas.paste(tile, (origin_x + column * cell, origin_y + row * cell), tile)
    return canvas


def write_preview(image, target, size=SIZE):
    """Save a preview as WebP, matching the catalog's existing convention."""
    if image is None:
        return None
    if image.size != (size, size):
        image = image.resize((size, size), Image.LANCZOS)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGBA").save(target, "WEBP", quality=82, method=4)
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="把 zipmod 的贴图渲染成预览图，落盘成 192px 的 PNG 便于肉眼比对。")
    parser.add_argument("packages", nargs="+", type=Path, help="一个或多个 .zipmod")
    parser.add_argument("--out", type=Path, default=Path("preview-renders"),
                        help="输出目录，默认 ./preview-renders")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for source in args.packages:
        image = render_package(source)
        print(source.name, "->", "none" if image is None else image.size)
        if image is not None:
            image.resize((192, 192), Image.LANCZOS).save(
                args.out / (source.stem[:26].replace(" ", "_") + ".png"))
