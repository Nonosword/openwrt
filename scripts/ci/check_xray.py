#!/usr/bin/env python3
"""Fail a firmware build if Xray's provider/version disagrees with PassWall2."""
import argparse
from pathlib import Path
import re
import sys


def version(value):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+~].*)?", value)
    if not match:
        raise ValueError(f"Unrecognized Xray version: {value!r}")
    return tuple(map(int, match.groups()))


def check(root, phase):
    recipe = root / "feeds/passwall_packages/xray-core"
    if phase == "prepare":
        # Xray 26.10.21 requires Go 1.27; openwrt-25.12 defaults to 1.26.
        # The versioned dependency also selects GO_HOST_VERSION in golang-values.mk.
        path = recipe / "Makefile"
        text = path.read_text()
        match = re.search(r"^PKG_BUILD_DEPENDS\s*:?=\s*(.*?)\s*$", text, re.M)
        if not match:
            raise ValueError("Cannot determine Xray host build dependencies")
        depends = match[1].split()
        if "golang/host" in depends:
            depends[depends.index("golang/host")] = "golang1.27/host"
        versions = [re.fullmatch(r"golang(\d+\.\d+)/host", item) for item in depends]
        versions = [item[1] for item in versions if item]
        if len(versions) != 1 or tuple(map(int, versions[0].split("."))) < (1, 27):
            raise ValueError(f"Xray needs a versioned Go >=1.27 dependency: {depends}")
        compiler = root / f"feeds/packages/lang/golang/golang{versions[0]}/Makefile"
        if not compiler.is_file():
            raise ValueError(f"Selected Go compiler is missing: {compiler}")
        path.write_text(text[:match.start()] + "PKG_BUILD_DEPENDS:=" + " ".join(depends) + text[match.end():])
        print(f"Selected Xray-only host dependency: golang{versions[0]}/host")
        return
    selected = list((root / "package/feeds").glob("*/xray-core"))
    if len(selected) != 1 or selected[0].resolve() != recipe.resolve():
        raise ValueError(f"Expected only passwall_packages/xray-core; selected: {selected}")
    recipe_text = (recipe / "Makefile").read_text()
    package = re.search(r"^PKG_VERSION\s*:?=\s*(\S+)\s*$", recipe_text, re.M)
    generator = root / "feeds/passwall2/luci-app-passwall2/luasrc/passwall2/util_xray.lua"
    minimum = re.search(r'local\s+xray_min_version\s*=\s*"([^"]+)"', generator.read_text())
    if not package or not minimum:
        raise ValueError("Cannot read Xray recipe version or PassWall2 minimum; update this check")
    package, minimum = package[1], minimum[1]
    if version(package) < version(minimum):
        raise ValueError(f"Xray {package} is older than PassWall2's minimum {minimum}")
    print(f"Xray provider=passwall_packages version={package} PassWall2 minimum={minimum}")
    if phase == "feeds":
        return
    config = (root / ".config").read_text().splitlines()
    if "CONFIG_PACKAGE_luci-app-passwall2=y" not in config or "CONFIG_PACKAGE_xray-core=y" not in config:
        print("PassWall2 with built-in Xray is not selected; skipping image check")
        return
    if phase == "config":
        return
    manifests = list((root / "bin/targets").glob("*/*/*.manifest"))
    if not manifests:
        raise ValueError("No firmware manifests found")
    for manifest in manifests:
        found = re.findall(r"^xray-core\s+-\s+(\S+)\s*$", manifest.read_text(), re.M)
        if len(found) != 1 or version(found[0]) != version(package):
            raise ValueError(f"{manifest}: expected Xray {package}, found {found}")
        print(f"Verified {manifest}: xray-core {found[0]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "feeds", "config", "image"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        check(args.root, args.phase)
    except (OSError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
