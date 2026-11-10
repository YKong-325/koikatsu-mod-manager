"""Validated local paths for the manager and its virtual library."""
import json
import os
from pathlib import Path
from AppPaths import APP_ROOT

CONFIG_FILE = APP_ROOT / "config.json"
RESOURCE_KEYS = ("chara", "coordinate", "studio")


def defaults(game=None):
    game = Path(game or APP_ROOT.parent).resolve()
    return {
        "game": str(game),
        "mods": str(game / "mods"),
        "userdata": str(game / "UserData"),
        "chara": str(game / "UserData" / "chara"),
        "coordinate": str(game / "UserData" / "coordinate"),
        "studio": str(game / "UserData" / "studio"),
        # Not a recycle bin: deleting a Mod renames it in place. This folder
        # only holds delete-log.jsonl, the list the restore page reads.
        "modtrash": str(game / "modtrash"),
    }


def read(path=CONFIG_FILE):
    values = defaults()
    if path.exists():
        stored = json.loads(path.read_text("utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("Invalid configuration file")
        values.update({key: stored[key] for key in values if key in stored})
    return values


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def validate(values, catalog):
    if not isinstance(values, dict):
        raise ValueError("目录配置格式不正确")
    clean = {}
    labels = {"game": "游戏", "mods": "Mod", "userdata": "UserData",
              "chara": "角色卡", "coordinate": "服装卡", "studio": "场景",
              "modtrash": "删除记录"}
    for key in defaults():
        value = values.get(key, "")
        if not isinstance(value, str):
            raise ValueError(labels[key] + "目录必须是文本")
        value = value.strip()
        if not value and key in (*RESOURCE_KEYS, "userdata"):
            clean[key] = ""
            continue
        path = Path(value)
        if not value or not path.is_absolute():
            raise ValueError(labels[key] + "目录需要填写完整路径")
        path = path.resolve()
        if key in ("game", "mods", *RESOURCE_KEYS) and not path.is_dir():
            raise ValueError(labels[key] + "目录不存在：" + str(path))
        if path.exists() and not path.is_dir():
            raise ValueError(labels[key] + "路径不是文件夹")
        clean[key] = str(path)
    roots = [Path(clean[key]) for key in ("mods", *RESOURCE_KEYS) if clean[key]]
    for index, root in enumerate(roots):
        if overlaps(root, catalog.resolve()):
            raise ValueError("资源目录不能与管理器索引目录重叠")
        if any(overlaps(root, other) for other in roots[index + 1:]):
            raise ValueError("Mod、角色卡、服装卡和场景目录不能相互包含")
    trash = Path(clean["modtrash"])
    if overlaps(trash, catalog.resolve()) or any(overlaps(trash, root) for root in roots):
        raise ValueError("删除记录目录不能与资源或索引目录重叠")
    if clean["userdata"] and overlaps(trash, Path(clean["userdata"])):
        raise ValueError("删除记录目录不能与 UserData 目录重叠")
    return clean


def save(values, path=CONFIG_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), "utf-8")
    os.replace(temporary, path)
