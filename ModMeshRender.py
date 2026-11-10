"""Render a preview picture for a package that ships none.

A fair number of costumes, hairs and accessories ship no thumbnail at all: the
author never packed one and the game folder has no copy either. What they do
always ship is the mesh and its texture, so the picture can be drawn locally
instead of borrowed from some character card that happens to use the package.

The renderer is deliberately simple and deterministic: gather the skinned mesh
renderers, draw every submesh triangle with its material's main texture, keep a
depth buffer, shade with two fixed lights, then frame the drawn pixels rather
than the vertex bounds (one stray vertex must not shrink the item to a dot).

The output is not a game screenshot: it is the item itself, on transparent
background, which is exactly how the existing previews read in the grid.
"""
import io
import math
import zipfile

import numpy as np
from PIL import Image

SIZE = 192
SUPERSAMPLE = 2
FILL = 0.86
ALPHA_CUTOFF = 0.25


def _bundle_bytes(archive, names):
    """Pick the bundle that most likely holds the model, preferring chara/."""
    bundles = [name for name in names
               if name.lower().endswith((".unity3d", ".unity"))
               and not name.lower().endswith(".manifest")]
    if not bundles:
        return None, None
    preferred = [name for name in bundles if "/chara/" in "/" + name.lower()]
    chosen = (preferred or bundles)[0]
    return chosen, archive.read(chosen)


def _mesh_arrays(mesh):
    from UnityPy.helpers.MeshHelper import MeshHandler

    handler = MeshHandler(mesh)
    handler.process()
    if not handler.m_Vertices:
        return None
    verts = np.array(handler.m_Vertices, dtype=np.float32)
    if not len(verts) or not np.isfinite(verts).all():
        return None
    normals = np.array(handler.m_Normals, dtype=np.float32) \
        if handler.m_Normals and len(handler.m_Normals) == len(verts) else None
    uvs = np.array(handler.m_UV0, dtype=np.float32) \
        if handler.m_UV0 and len(handler.m_UV0) == len(verts) else None
    indices = np.array(handler.m_IndexBuffer or [], dtype=np.int32)
    indices = indices[:(len(indices) // 3) * 3]
    if not len(indices):
        return None
    return verts, normals, uvs, indices


def _main_texture(material):
    props = getattr(material, "m_SavedProperties", None)
    if not props:
        return None
    for entry in props.m_TexEnvs:
        if entry[0] in ("_MainTex", "_BaseMap"):
            pointer = entry[1].m_Texture
            if pointer and getattr(pointer, "path_id", 0):
                return pointer
    return None


def _submesh_span(mesh, sub):
    width = 4 if getattr(mesh, "m_IndexFormat", 0) == 1 else 2
    return sub.firstByte // width, sub.indexCount


def collect_parts(data):
    """One entry per drawn submesh: geometry plus the texture its material uses."""
    import UnityPy

    env = UnityPy.load(io.BytesIO(data) if isinstance(data, bytes) else data)
    materials, meshes, textures, renderers = {}, {}, {}, []
    filters = {}
    for obj in env.objects:
        kind = obj.type.name
        try:
            if kind == "Material":
                materials[obj.path_id] = obj.read()
            elif kind == "Mesh":
                meshes[obj.path_id] = obj.read()
            elif kind == "Texture2D":
                textures[obj.path_id] = obj.read()
            elif kind in ("SkinnedMeshRenderer", "MeshRenderer"):
                renderers.append(obj.read())
            elif kind == "MeshFilter":
                filter_object = obj.read()
                owner = getattr(getattr(filter_object, "m_GameObject", None), "path_id", None)
                pointer = getattr(filter_object, "m_Mesh", None)
                if owner and pointer is not None:
                    filters[owner] = getattr(pointer, "path_id", None)
        except Exception:
            continue

    parts = []
    for renderer in renderers:
        pointer = getattr(renderer, "m_Mesh", None)
        mesh_id = getattr(pointer, "path_id", None)
        if not mesh_id:
            owner = getattr(getattr(renderer, "m_GameObject", None), "path_id", None)
            mesh_id = filters.get(owner)
        mesh = meshes.get(mesh_id)
        if mesh is None:
            continue
        arrays = _mesh_arrays(mesh)
        if arrays is None:
            continue
        material_ptrs = list(getattr(renderer, "m_Materials", None) or [])
        for index, sub in enumerate(mesh.m_SubMeshes or []):
            first, count = _submesh_span(mesh, sub)
            if not count:
                continue
            material = None
            if index < len(material_ptrs):
                material = materials.get(getattr(material_ptrs[index], "path_id", None))
            texture = None
            pointer_tex = _main_texture(material) if material is not None else None
            if pointer_tex is not None:
                texture = textures.get(getattr(pointer_tex, "path_id", None))
            parts.append((arrays, first, count, texture, mesh.m_Name or ""))
    if not parts:
        # No renderer names a material: draw every mesh with the largest texture
        # in the bundle, which is nearly always the one the author painted on.
        fallback, best_area = None, -1
        for texture in textures.values():
            try:
                area = int(texture.m_Width) * int(texture.m_Height)
            except Exception:
                continue
            if area > best_area:
                fallback, best_area = texture, area
        for mesh in meshes.values():
            arrays = _mesh_arrays(mesh)
            if arrays is None:
                continue
            parts.append((arrays, 0, len(arrays[3]), fallback, mesh.m_Name or ""))
    return parts


def _texture_array(texture):
    if texture is None:
        return None
    try:
        return np.asarray(texture.image.convert("RGBA"), dtype=np.float32) / 255.0
    except Exception:
        return None


def _draw(parts, size, center, scale):
    color = np.zeros((size, size, 3), dtype=np.float32)
    alpha = np.zeros((size, size), dtype=np.float32)
    depth_buffer = np.full((size, size), -np.inf, dtype=np.float32)
    key_light = np.array([0.38, 0.52, 0.76], dtype=np.float32)
    key_light /= np.linalg.norm(key_light)
    fill_light = np.array([-0.55, -0.15, 0.42], dtype=np.float32)
    fill_light /= np.linalg.norm(fill_light)
    edge = size - 1
    drawn = 0

    for (verts, normals, uvs, indices), first, count, texture, _name in parts:
        triangles = indices[first:first + count]
        triangles = triangles[:(len(triangles) // 3) * 3].reshape(-1, 3)
        if not len(triangles):
            continue
        texture_pixels = _texture_array(texture)
        screen_x = ((verts[:, 0] - center[0]) * scale * 0.5 + 0.5) * edge
        screen_y = (0.5 - (verts[:, 1] - center[1]) * scale * 0.5) * edge
        total = len(verts)
        for triangle in triangles:
            i0, i1, i2 = int(triangle[0]), int(triangle[1]), int(triangle[2])
            if i0 >= total or i1 >= total or i2 >= total:
                continue
            x0, x1, x2 = screen_x[i0], screen_x[i1], screen_x[i2]
            y0, y1, y2 = screen_y[i0], screen_y[i1], screen_y[i2]
            min_x = int(math.floor(min(x0, x1, x2)))
            max_x = int(math.ceil(max(x0, x1, x2)))
            min_y = int(math.floor(min(y0, y1, y2)))
            max_y = int(math.ceil(max(y0, y1, y2)))
            if max_x < 0 or max_y < 0 or min_x > edge or min_y > edge:
                continue
            min_x, min_y = max(min_x, 0), max(min_y, 0)
            max_x, max_y = min(max_x, edge), min(max_y, edge)
            if min_x > max_x or min_y > max_y:
                continue
            ys, xs = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
            determinant = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(determinant) < 1e-9:
                continue
            w0 = ((y1 - y2) * (xs - x2) + (x2 - x1) * (ys - y2)) / determinant
            w1 = ((y2 - y0) * (xs - x2) + (x0 - x2) * (ys - y2)) / determinant
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -0.0015) & (w1 >= -0.0015) & (w2 >= -0.0015)
            if not inside.any():
                continue
            depth = (w0 * verts[i0, 2] + w1 * verts[i1, 2] + w2 * verts[i2, 2])
            window = depth_buffer[min_y:max_y + 1, min_x:max_x + 1]
            keep = inside & (depth > window)
            if not keep.any():
                continue
            if texture_pixels is not None and uvs is not None:
                u = w0 * uvs[i0, 0] + w1 * uvs[i1, 0] + w2 * uvs[i2, 0]
                v = w0 * uvs[i0, 1] + w1 * uvs[i1, 1] + w2 * uvs[i2, 1]
                height, width = texture_pixels.shape[:2]
                column = np.clip((u % 1.0 * width).astype(np.int32), 0, width - 1)
                row = np.clip(((1.0 - v % 1.0) * height).astype(np.int32), 0, height - 1)
                surface = texture_pixels[row, column, :3]
                coverage = texture_pixels[row, column, 3]
            else:
                surface = np.full(keep.shape + (3,), 0.68, dtype=np.float32)
                coverage = np.ones(keep.shape, dtype=np.float32)
            keep = keep & (coverage >= ALPHA_CUTOFF)
            if not keep.any():
                continue
            if normals is not None:
                normal = (w0[..., None] * normals[i0] + w1[..., None] * normals[i1]
                          + w2[..., None] * normals[i2])
                length = np.linalg.norm(normal, axis=-1, keepdims=True)
                normal = normal / np.maximum(length, 1e-6)
                shade = (0.46 + 0.42 * np.abs(normal @ key_light)
                         + 0.12 * np.abs(normal @ fill_light))
                shade = np.clip(shade, 0.42, 1.0)
            else:
                shade = np.full(keep.shape, 0.9, dtype=np.float32)
            window_color = color[min_y:max_y + 1, min_x:max_x + 1]
            color[min_y:max_y + 1, min_x:max_x + 1] = np.where(
                keep[..., None], surface * shade[..., None], window_color)
            alpha[min_y:max_y + 1, min_x:max_x + 1] = np.where(
                keep, 1.0, alpha[min_y:max_y + 1, min_x:max_x + 1])
            depth_buffer[min_y:max_y + 1, min_x:max_x + 1] = np.where(
                keep, depth, depth_buffer[min_y:max_y + 1, min_x:max_x + 1])
            drawn += 1
    if not drawn:
        return None
    rgba = np.dstack([np.clip(color, 0.0, 1.0), alpha])
    return Image.fromarray((rgba * 255.0 + 0.5).astype(np.uint8))


def _trim(mass, share):
    """How many leading slices hold less than `share` of the pixels."""
    limit = float(mass.sum()) * share
    removed, index = 0.0, 0
    while index < len(mass) // 3 and removed + float(mass[index]) <= limit:
        removed += float(mass[index])
        index += 1
    return index


def _content_box(image, trim=0.03):
    """Bounding box of what was actually drawn, ignoring stray specks.

    A costume whose parts hang off different bones can include one small piece
    far from the rest; framing the union would shrink the real item to a stamp.
    Trimming the sparse edges drops those specks without touching a genuine
    thin part, which always carries more pixels than the trim allowance.
    """
    alpha = np.asarray(image.getchannel("A")).astype(np.float32)
    mask = alpha > 24
    if not mask.any():
        return None
    rows = np.where(mask.any(axis=1))[0]
    columns = np.where(mask.any(axis=0))[0]
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(columns[0]), int(columns[-1]) + 1
    if trim > 0:
        window = mask[top:bottom, left:right]
        row_mass = window.sum(axis=1)
        column_mass = window.sum(axis=0)
        top += _trim(row_mass, trim)
        bottom -= _trim(row_mass[::-1], trim)
        left += _trim(column_mass, trim)
        right -= _trim(column_mass[::-1], trim)
    if right - left < 2 or bottom - top < 2:
        return None
    return (left, top, right, bottom)


def render(parts, size=SIZE, supersample=SUPERSAMPLE):
    """Frame the item by what it actually covers, not by its vertex bounds.

    A first pass at a loose fit finds the drawn pixels; the second pass frames
    that box, which is what keeps one stray vertex from shrinking the render to
    a stamp in the corner.
    """
    if not parts:
        return None
    coarse = size * supersample
    verts = np.concatenate([part[0][0] for part in parts])
    low, high = verts[:, :2].min(axis=0), verts[:, :2].max(axis=0)
    span = float(np.max(np.maximum(high - low, 1e-6)))
    center = (low + high) / 2.0
    scale = 1.6 / span
    first = _draw(parts, coarse, center, scale)
    if first is None:
        return None
    box = _content_box(first)
    if box is None:
        return None
    left, top, right, bottom = box
    world_width = (right - left) / (coarse - 1) * 2.0 / scale
    world_height = (bottom - top) / (coarse - 1) * 2.0 / scale
    center = (center[0] + ((left + right) / 2.0 / (coarse - 1) - 0.5) * 2.0 / scale,
              center[1] - ((top + bottom) / 2.0 / (coarse - 1) - 0.5) * 2.0 / scale)
    scale = 2.0 * FILL / max(world_width, world_height, 1e-6)
    final = _draw(parts, coarse, center, scale)
    if final is None:
        return None
    return final.resize((size, size), Image.LANCZOS)


def render_package(path, size=SIZE):
    """Render one .zipmod/.zip; returns a PIL image or None when undrawable."""
    try:
        with zipfile.ZipFile(path) as archive:
            name, data = _bundle_bytes(archive, archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return None
    if data is None:
        return None
    try:
        parts = collect_parts(data)
    except Exception:
        return None
    return render(parts, size=size)
