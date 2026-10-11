"""Display-only release identities. Git/CI, never version ordering, authorizes updates."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
import re

VERSION = re.compile(r"^\d{4}\.\d{2}\.\d{2}\.\d+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def parse_catalog(text):
    if len(text) > 256_000:
        raise ValueError("Release catalog too large")
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("history"), list):
        raise ValueError("Invalid release catalog")
    build = data.get("build", {})
    if not isinstance(build, dict) or not VERSION.fullmatch(str(build.get("version", ""))):
        raise ValueError("Invalid build version")
    date.fromisoformat(build.get("date", ""))
    seen = set()
    for row in data["history"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid release row")
        pr = row.get("pr")
        if not isinstance(pr, int) or isinstance(pr, bool) or pr < 1 or pr in seen:
            raise ValueError("Duplicate or invalid PR")
        seen.add(pr)
        if row.get("status") not in {"released", "included", "preview", "not_released"}:
            raise ValueError("Invalid release state")
        if row.get("version") is not None and not VERSION.fullmatch(row["version"]):
            raise ValueError("Invalid release version")
        date.fromisoformat(row.get("date", ""))
        if row.get("version") and row["version"] != row["date"].replace("-", ".") + "." + str(pr):
            raise ValueError("Version does not match date and PR")
        if not isinstance(row.get("notes"), list) or not all(isinstance(n, str) for n in row["notes"]):
            raise ValueError("Invalid release notes")
        if row.get("commit") and not COMMIT.fullmatch(row["commit"]):
            raise ValueError("Invalid release commit")
    if build.get("pr") not in seen:
        raise ValueError("Build has no changelog")
    if build["version"] != build["date"].replace("-", ".") + "." + str(build["pr"]):
        raise ValueError("Build version mismatch")
    return data


def read_catalog(root):
    try:
        path = Path(root) / "release_catalog.json"
        if path.stat().st_size > 256_000:
            return None
        return parse_catalog(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def identity(root, local=None, *, target=None, git_reader=None):
    """Unknown old builds stay unknown; matching remote content belongs to exact SHA."""
    catalog = read_catalog(root)
    sha = target or (local or {}).get("head", "")
    exact = next((r for r in (catalog or {}).get("history", [])
                  if r.get("status") == "released" and r.get("commit") == sha), None)
    # Prefer the manifest committed at the exact target, not the local preview build.
    if target and git_reader and COMMIT.fullmatch(target):
        try:
            remote = parse_catalog(git_reader(target, "release_catalog.json"))
            build = remote["build"]
            row = next(r for r in remote["history"] if r["pr"] == build["pr"])
            return dict(version=build["version"], date=build["date"], notes=row["notes"],
                        channel="main", head=sha)
        except (OSError, ValueError, TypeError, AttributeError, StopIteration):
            pass
    if exact:
        return dict(version=exact["version"], date=exact["date"], notes=exact["notes"],
                    channel="main", head=sha)
    if not target and catalog:
        build = catalog["build"]
        row = next(r for r in catalog["history"] if r["pr"] == build["pr"])
        return dict(version=build["version"], date=build["date"], notes=row["notes"],
                    channel="preview" if (local or {}).get("branch") != "main" else "main",
                    head=sha)
    return dict(version=None, date=None, notes=[], channel="unknown", head=sha)


def version_label(release):
    if not release or not release.get("version"):
        return "未编号版本（旧安装）"
    suffix = " · 预览版" if release.get("channel") == "preview" else ""
    return "v" + release["version"] + suffix


def presentation(result, running=None):
    """Beginner-facing conclusions; technical errors/details never become main copy."""
    state = result.get("state", "unavailable")
    current = running or result.get("local_release", {})
    local = result.get("local", {})
    changed = bool(running and running.get("head") and local.get("head")
                   and running["head"] != local["head"])
    title, message = {
        "current": ("当前无需更新", "当前软件与检查到的主线提交一致。"),
        "ahead": ("当前使用本机修复版", "正式发布版尚未包含本机提交。可继续使用，不会退回旧版本。"),
        "diverged": ("需要维护者核对版本", "本机与正式版各有改动，为保留本机修复，暂不自动替换。"),
        "pending": ("新版尚在验收", "新代码尚未通过验收，暂不提供更新；当前版本可以继续使用。"),
        "unavailable": ("暂时无法检查版本", "请稍后重新检查；不影响行情更新和策略扫描。"),
        "available": ("发现软件新版", "更新软件将保留原行情与交易记录，需要重新启动。"),
    }.get(state, ("版本状态待确认", "暂不自动更新，当前版本可以继续使用。"))
    allowed = bool(result.get("can_update")) and not changed
    if changed:
        title, message = "代码已更换，需要重新启动", "当前窗口仍运行启动时的版本。请先保存编辑，从托盘完整退出后重新打开。"
    elif state == "current" and local.get("dirty"):
        title, message = "当前安装有本机定制", "本机保留了修改或新增文件，暂不能确认与正式版完全一致；不会自动覆盖。"
    elif state == "available" and not allowed:
        reason = result.get("block_reason")
        message = {
            "modified": "本机保留了修改或新增文件，请维护者先核对；不会自动覆盖。",
            "development": "当前是预览/开发安装，请维护者先对齐正式版，保留已有修复。",
            "manual": "新版涉及依赖、数据结构或受保护文件，需要维护者升级并验收。",
            "cached": "这是之前的检查结果，请点击重新检查后再更新。",
        }.get(reason, "目前尚不满足安全更新条件，请重新检查或让维护者核对。")
    target = result.get("target_release", {})
    target_label = version_label(target) if target.get("version") else "版本号待确认"
    return dict(title=title, message=message, current=version_label(current),
                target=target_label if result.get("target") else "未能确认", can_update=allowed,
                notes=result.get("target_release", {}).get("notes", [])[:4])


def changelog_text(catalog):
    lines = ["# Aplus 更新日志", "", "发布编号按日期＋PR号回溯整理，并非当时已发布过的Git标签。",
             "1.1.4等旧编号保留为功能阶段名称；新编号用于准确识别每轮代码。",
             "未合并修复列为预览，纳入其他PR的改动单列但不重复算独立发布。", ""]
    labels = {"released": "已进入主线（历史回溯）", "included": "随其他PR纳入主线",
              "preview": "预览修复，待合入主线", "not_released": "未发布"}
    for row in reversed(catalog["history"]):
        version = "v" + row["version"] if row["version"] else "无发布编号"
        if row["status"] == "preview":
            version += "-preview"
        lines += [f"## {version} · {row['date']} · PR{row['pr']}", "",
                  labels[row["status"]] + (f"（随PR{row['included_in']}）" if row.get("included_in") else ""), ""]
        lines += ["- " + n for n in row["notes"]]
        lines += ["", f"[对应PR]({row['url']})", ""]
    return "\n".join(lines)
