import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional


class LuaBridge:

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self._lua_bin = shutil.which("lua")
        self._helper = Path(__file__).parent / "parse_config.lua"

    def load_config(self) -> dict:
        if not self.config_path.exists():
            return self._default_config()

        result = self._load_with_subprocess()
        if result is not None:
            return result
        return self._load_simple()

    def _parse_config_text(self, text: str) -> dict:
        config = self._default_config()

        for key in ("mirror", "suite", "root", "arch", "components"):
            m = re.search(rf'{key}\s*=\s*"([^"]*)"', text)
            if m:
                config[key] = m.group(1)

        components = re.search(r'components\s*=\s*\{([^}]*)\}', text)
        if components:
            comps = re.findall(r'"([^"]*)"', components.group(1))
            if comps:
                config["components"] = comps

        architectures = re.search(r'architectures\s*=\s*\{([^}]*)\}', text)
        if architectures:
            archs = re.findall(r'"([^"]*)"', architectures.group(1))
            if archs:
                config["architectures"] = archs

        return config

    def _load_simple(self) -> dict:
        with open(self.config_path, "r") as f:
            text = f.read()
        return self._parse_config_text(text)

    def _load_with_subprocess(self) -> Optional[dict]:
        if not self._lua_bin or not self._helper.exists():
            return None
        try:
            result = subprocess.run(
                [self._lua_bin, str(self._helper), str(self.config_path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return None
            return self._parse_config_text(result.stdout)
        except (subprocess.SubprocessError, OSError):
            return None

    def _detect_arch(self) -> str:
        arch_env = os.environ.get("TANUKI_ARCH", "").lower()
        if arch_env:
            return arch_env
        try:
            uname = os.uname().machine.lower()
        except AttributeError:
            return "amd64"
        mapping = {
            "x86_64": "amd64", "amd64": "amd64",
            "aarch64": "arm64", "arm64": "arm64",
            "armv7l": "armhf", "armv8l": "armhf", "arm": "armhf",
            "i386": "i386", "i686": "i386",
            "riscv64": "riscv64", "s390x": "s390x",
            "ppc64le": "ppc64el", "ppc64": "ppc64",
        }
        return mapping.get(uname, "amd64")

    def _detect_suite(self) -> str:
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("VERSION_CODENAME="):
                        return line.strip().split("=", 1)[1].strip("\"'")
                    if line.startswith("VERSION_ID="):
                        ver = line.strip().split("=", 1)[1].strip("\"'")
                        return f"v{ver}"
        except (FileNotFoundError, PermissionError):
            pass
        return "sid"

    def _default_config(self) -> dict:
        return {
            "mirror": "https://deb.debian.org/debian",
            "suite": self._detect_suite(),
            "root": "/",
            "arch": self._detect_arch(),
            "components": ["main", "contrib", "non-free-firmware", "non-free"],
            "architectures": [self._detect_arch()],
            "cache_dir": "/var/cache/tanuki",
            "db_dir": "/var/lib/tanuki",
        }
