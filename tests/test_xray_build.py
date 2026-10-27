"""Isolated firmware check fixtures; no feed updates or builds."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_xray", REPO / "scripts/ci/check_xray.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class XrayBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.write("feeds/passwall_packages/xray-core/Makefile", "PKG_VERSION:=26.10.21\n")
        self.write("feeds/passwall2/luci-app-passwall2/luasrc/passwall2/util_xray.lua", 'local xray_min_version = "26.7.11"\n')
        self.write(".config", "CONFIG_PACKAGE_luci-app-passwall2=y\nCONFIG_PACKAGE_xray-core=y\n")
        self.manifest = "bin/targets/rockchip/armv8/test.manifest"
        self.write(self.manifest, "xray-core - 26.10.21-r1\n")
        self.link = self.root / "package/feeds/passwall_packages/xray-core"
        self.link.parent.mkdir(parents=True)
        self.link.symlink_to("../../../feeds/passwall_packages/xray-core")

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def test_correct_image(self):
        for phase in ("feeds", "config", "image"):
            checks.check(self.root, phase)

    def test_wrong_provider(self):
        self.link.unlink()
        self.link.symlink_to("../../../feeds/packages/net/xray-core")
        with self.assertRaises(ValueError): checks.check(self.root, "feeds")

    def test_duplicate_provider(self):
        self.write("package/feeds/packages/xray-core/Makefile", "PKG_VERSION:=26.3.27\n")
        with self.assertRaises(ValueError): checks.check(self.root, "feeds")

    def test_old_recipe(self):
        self.write("feeds/passwall_packages/xray-core/Makefile", "PKG_VERSION:=26.3.27\n")
        with self.assertRaises(ValueError): checks.check(self.root, "feeds")

    def test_missing_minimum(self):
        self.write("feeds/passwall2/luci-app-passwall2/luasrc/passwall2/util_xray.lua", "")
        with self.assertRaises(ValueError): checks.check(self.root, "feeds")

    def test_bad_image(self):
        for text in ("", "xray-core - 26.3.27-r1\n", "xray-core - 26.10.22-r1\n"):
            self.write(self.manifest, text)
            with self.assertRaises(ValueError): checks.check(self.root, "image")

    def test_missing_manifest(self):
        (self.root / self.manifest).unlink()
        with self.assertRaises(ValueError): checks.check(self.root, "image")

    def test_optional_package(self):
        self.write(".config", "# CONFIG_PACKAGE_xray-core is not set\n")
        (self.root / self.manifest).unlink()
        checks.check(self.root, "image")

    def test_real_feed_installer(self):
        # Exercise the real Perl installer; only metadata generation is replaced
        # by a tiny make fixture. Feed priority/dependency selection is untouched.
        for name in ("feeds", "metadata.pm"):
            dst = self.root / "scripts" / name
            dst.parent.mkdir(exist_ok=True)
            shutil.copy(REPO / "scripts" / name, dst)
        self.write("feeds.conf.default", "src-link packages feeds/packages\nsrc-link passwall_packages feeds/passwall_packages\n")
        for feed, path in (("packages", "net/xray-core"), ("passwall_packages", "xray-core")):
            self.write(f"feeds/{feed}/{path}/Makefile", "# fixture\n")
            self.write(f"feeds/{feed}.index", f"Source-Makefile: feeds/{feed}/{path}/Makefile\nPackage: xray-core\n\n")
            self.write(f"feeds/{feed}.targetindex", "")
        self.write("tmp/.targetinfo", "")
        self.write("Makefile", "prepare-tmpinfo:\n\t@mkdir -p tmp\n\t@: > tmp/.packageinfo\n\t@for p in package/feeds/*/xray-core; do [ ! -L $$p ] || printf 'Source-Makefile: %s/Makefile\\nPackage: xray-core\\n\\n' $$p >> tmp/.packageinfo; done\n")
        self.link.unlink()
        env = dict(os.environ, TOPDIR=str(self.root))
        def install(*args):
            result = subprocess.run(["perl", str(self.root / "scripts/feeds"), "install", *args], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        install("-a")
        old = self.root / "package/feeds/packages/xray-core"
        self.assertTrue(old.is_symlink(), "old workflow must reproduce official feed selection")
        old.unlink()
        install("-p", "passwall_packages", "xray-core")
        install("-a")
        self.assertTrue(self.link.is_symlink())
        self.assertFalse(old.exists(), "install-all must not reintroduce the old provider")

    def test_xray_only_go_dependency(self):
        path = "feeds/passwall_packages/xray-core/Makefile"
        self.write(path, "PKG_BUILD_DEPENDS:=golang/host other/host\ninclude golang-package.mk\n")
        self.write("feeds/packages/lang/golang/golang1.27/Makefile", "# compiler\n")
        self.write("feeds/packages/lang/golang/golang-values.mk", "GO_DEFAULT_VERSION:=1.26\n")
        checks.check(self.root, "prepare")
        self.assertIn("PKG_BUILD_DEPENDS:=golang1.27/host other/host\n", (self.root / path).read_text())
        self.assertEqual((self.root / "feeds/packages/lang/golang/golang-values.mk").read_text(), "GO_DEFAULT_VERSION:=1.26\n")
        checks.check(self.root, "prepare")  # idempotent

    def test_missing_go_compiler(self):
        self.write("feeds/passwall_packages/xray-core/Makefile", "PKG_BUILD_DEPENDS:=golang/host\n")
        with self.assertRaises(ValueError): checks.check(self.root, "prepare")

    def test_preserve_newer_explicit_go(self):
        self.write("feeds/passwall_packages/xray-core/Makefile", "PKG_BUILD_DEPENDS:=golang1.28/host\n")
        self.write("feeds/packages/lang/golang/golang1.28/Makefile", "# compiler\n")
        checks.check(self.root, "prepare")
        self.assertIn("golang1.28/host", (self.root / "feeds/passwall_packages/xray-core/Makefile").read_text())


if __name__ == "__main__":
    unittest.main()
