"""Prepare an isolated, pinned experimental SDK copy without importing vendor code."""

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

UPSTREAM_COMMIT = "bd7c5233e79c0a185638a325bf6d30607244dfa8"
MANIFEST = "jiahui-provenance.json"


def patch_update_deletion(source: str) -> str:
    tree = ast.parse(source)
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "delete_update_files"
    ]
    if len(functions) != 1:
        raise ValueError("expected exactly one update deletion function")
    node = functions[0]
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1 : node.end_lineno] = [
        "def delete_update_files():\n",
        '    """Disabled by Jiahui read-only experiment: never delete WeChat updates."""\n',
        "    return None\n",
    ]
    result = "".join(lines)
    ast.parse(result)
    return result


def git(source: Path, *args) -> bytes:
    return subprocess.run(
        ["git", "-C", str(source), *args],
        check=True,
        capture_output=True,
    ).stdout


def prepare(source: Path, destination: Path):
    if destination.exists():
        raise ValueError("destination exists; preserve it and use a new path")
    if git(source, "rev-parse", "HEAD").decode().strip() != UPSTREAM_COMMIT:
        raise ValueError("upstream commit differs from reviewed revision")
    names = git(source, "ls-tree", "-r", "--name-only", "-z", UPSTREAM_COMMIT).decode().split("\0")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="wxauto4-stage-", dir=destination.parent))
    try:
        hashes = {}
        originals = {}
        for name in filter(None, names):
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                raise ValueError("invalid source tree path")
            # Read committed blobs, never user edits, untracked files or symlink targets.
            content = git(source, "show", f"{UPSTREAM_COMMIT}:{name}")
            originals[name] = hashlib.sha256(content).hexdigest()
            if name == "wxauto4/utils/tools.py":
                content = patch_update_deletion(content.decode("utf-8")).encode("utf-8")
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            hashes[name] = hashlib.sha256(content).hexdigest()
        if "wxauto4/utils/tools.py" not in hashes:
            raise ValueError("missing reviewed patch target")
        (temporary / MANIFEST).write_text(
            json.dumps(
                {
                    "upstream_commit": UPSTREAM_COMMIT,
                    "original_sha256": originals,
                    "prepared_sha256": hashes,
                    "patch": "disable-update-deletion-v1",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def verify(bundle: Path):
    data = json.loads((bundle / MANIFEST).read_text(encoding="utf-8"))
    if data["upstream_commit"] != UPSTREAM_COMMIT or data["patch"] != "disable-update-deletion-v1":
        raise ValueError("unreviewed bundle")
    for name, expected in data["prepared_sha256"].items():
        path = (bundle / name).resolve(strict=True)
        if (
            not path.is_relative_to(bundle.resolve())
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("bundle integrity check failed")
    # Fail closed if an editable installation or later edit added executable source.
    expected_python = {n for n in data["prepared_sha256"] if n.endswith(".py")}
    actual_python = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*.py")}
    if actual_python != expected_python:
        raise ValueError("unexpected Python source in bundle")
    tools = (bundle / "wxauto4/utils/tools.py").read_text(encoding="utf-8")
    fn = next(
        n
        for n in ast.parse(tools).body
        if isinstance(n, ast.FunctionDef) and n.name == "delete_update_files"
    )
    if (
        len(fn.body) != 2
        or not isinstance(fn.body[1], ast.Return)
        or not isinstance(fn.body[1].value, ast.Constant)
        or fn.body[1].value.value is not None
    ):
        raise ValueError("update deletion patch missing")
    return data


def main():
    parser = argparse.ArgumentParser(description="准备固定版本 wxauto4 试验副本，不导入/运行 SDK")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, args.destination)
    verify(args.destination)
    print("已准备并校验试验副本；尚未连接微信。")


if __name__ == "__main__":
    main()
