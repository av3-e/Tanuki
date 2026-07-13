import os
import re
from pathlib import Path
from typing import Dict, Optional


class LuaBridge:

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self._use_lua = False
        self._lupa = None

        try:
            import lupa
            self._lupa = lupa
            self._use_lua = True
        except ImportError:
            pass

    def load_config(self) -> dict:
        if not self.config_path.exists():
            return self._default_config()

        if self._use_lua:
            return self._load_with_lupa()
        return self._load_simple()

    def _load_simple(self) -> dict:
        config = self._default_config()
        with open(self.config_path, "r") as f:
            text = f.read()

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

    def _load_with_lupa(self) -> dict:
        config = self._default_config()
        try:
            with open(self.config_path, "r") as f:
                code = f.read()

            lua = self._lupa.LuaRuntime(unpack_returned_tuples=True)
            lua.execute(code)
            globals_table = lua.globals()

            for key in ("mirror", "suite", "root", "arch", "components", "architectures"):
                try:
                    val = globals_table[key]
                except KeyError:
                    continue
                if val is not None:
                    if hasattr(val, "__iter__") and not isinstance(val, str):
                        if hasattr(val, "__len__"):
                            val = list(val)
                        else:
                            val = [str(val)]
                    config[key] = val

        except Exception:
            return self._load_simple()

        return config

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
