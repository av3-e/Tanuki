import gzip
import lzma
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Iterator
from urllib.request import urlopen, Request
from urllib.parse import urljoin


@dataclass
class RepoPackage:
    package: str
    version: str
    architecture: str
    maintainer: str = ""
    description: str = ""
    section: str = ""
    priority: str = "optional"
    installed_size: int = 0
    filename: str = ""
    size: int = 0
    md5sum: str = ""
    sha256: str = ""
    depends: str = ""
    pre_depends: str = ""
    recommends: str = ""
    suggests: str = ""
    conflicts: str = ""
    replaces: str = ""
    provides: str = ""
    breaks: str = ""
    enhances: str = ""
    essential: str = "no"
    homepage: str = ""
    raw: Dict[str, str] = field(default_factory=dict)


def parse_packages(text: str) -> List[RepoPackage]:
    packages = []
    current: Dict[str, str] = {}
    current_key = None
    current_value = []

    for line in text.split("\n"):
        if line == "" and current_key is not None:
            if current_key is not None:
                current[current_key] = " ".join(v for v in current_value if v).strip()
            if current:
                packages.append(_dict_to_pkg(current))
            current = {}
            current_key = None
            current_value = []
            continue

        if line.startswith(" ") or line.startswith("\t"):
            if current_key is not None:
                current_value.append(line.strip())
        elif ":" in line:
            if current_key is not None:
                current[current_key] = " ".join(v for v in current_value if v).strip()
            key, _, value = line.partition(":")
            current_key = key.strip()
            current_value = [value.strip()]
        else:
            if current_key is not None:
                current_value.append(line.strip())

    if current_key is not None:
        current[current_key] = " ".join(v for v in current_value if v).strip()
    if current:
        packages.append(_dict_to_pkg(current))

    return packages


def _dict_to_pkg(d: Dict[str, str]) -> RepoPackage:
    key_map = {k.lower().replace("-", "_"): k for k in d}

    def get_field(name: str, default=""):
        real_key = key_map.get(name, name)
        return d.get(real_key, default)

    return RepoPackage(
        package=get_field("package", ""),
        version=get_field("version", ""),
        architecture=get_field("architecture", ""),
        maintainer=get_field("maintainer", ""),
        description=get_field("description", ""),
        section=get_field("section", ""),
        priority=get_field("priority", "optional"),
        installed_size=int(get_field("installed_size", "0") or "0"),
        filename=get_field("filename", ""),
        size=int(get_field("size", "0") or "0"),
        md5sum=get_field("md5sum", ""),
        sha256=get_field("sha256", ""),
        depends=get_field("depends", ""),
        pre_depends=get_field("pre_depends", ""),
        recommends=get_field("recommends", ""),
        suggests=get_field("suggests", ""),
        conflicts=get_field("conflicts", ""),
        replaces=get_field("replaces", ""),
        provides=get_field("provides", ""),
        breaks=get_field("breaks", ""),
        enhances=get_field("enhances", ""),
        essential=get_field("essential", "no"),
        homepage=get_field("homepage", ""),
        raw=d,
    )


class RepositoryIndex:

    def __init__(self):
        self._packages: Dict[str, List[RepoPackage]] = {}

    def add(self, packages: List[RepoPackage]):
        for pkg in packages:
            self._packages.setdefault(pkg.package.lower(), []).append(pkg)

    def get(self, name: str) -> List[RepoPackage]:
        return self._packages.get(name.lower(), [])

    def search(self, query: str) -> List[RepoPackage]:
        q = query.lower()
        results = []
        for name, pkgs in self._packages.items():
            if q in name:
                results.extend(pkgs)
            else:
                for p in pkgs:
                    if q in p.description.lower():
                        results.append(p)
                        break
        return results

    def all_packages(self) -> Iterator[RepoPackage]:
        for pkgs in self._packages.values():
            yield from pkgs

    def unique_names(self) -> List[str]:
        return sorted(self._packages.keys())

    def __len__(self) -> int:
        return sum(len(v) for v in self._packages.values())


class Repository:

    def __init__(self, base_url: str, suite: str = "forky",
                 components: List[str] = None,
                 architectures: List[str] = None):
        self.base_url = base_url.rstrip("/")
        self.suite = suite
        self.components = components or ["main", "contrib", "non-free-firmware", "non-free"]
        self.architectures = architectures or ["amd64"]
        self.index = RepositoryIndex()

    def _packages_url(self, component: str, arch: str) -> str:
        return (
            f"{self.base_url}/dists/{self.suite}/{component}/"
            f"binary-{arch}/Packages.xz"
        )

    def _packages_gz_url(self, component: str, arch: str) -> str:
        return (
            f"{self.base_url}/dists/{self.suite}/{component}/"
            f"binary-{arch}/Packages.gz"
        )

    def _deb_url(self, filename: str) -> str:
        return f"{self.base_url}/{filename}"

    def update(self):
        self.index = RepositoryIndex()
        headers = {"User-Agent": "Tanuki/0.2.0"}

        for comp in self.components:
            for arch in self.architectures:
                url = self._packages_url(comp, arch)
                text = _fetch_decompressed(url, headers)
                if text is None:
                    url = self._packages_gz_url(comp, arch)
                    text = _fetch_decompressed(url, headers)
                if text is None:
                    continue
                packages = parse_packages(text)
                self.index.add(packages)

    def verify_release(self, keyring: Optional[Path] = None) -> bool:
        if os.environ.get("TANUKI_SKIP_GPG"):
            return True

        base = f"{self.base_url}/dists/{self.suite}"
        headers = {"User-Agent": "Tanuki/0.2.0"}

        inrelease_url = f"{base}/InRelease"
        inrelease = _fetch_raw(inrelease_url, headers)
        if inrelease is not None:
            return _verify_inrelease(inrelease, keyring)

        release_url = f"{base}/Release"
        gpg_url = f"{base}/Release.gpg"
        release_data = _fetch_raw(release_url, headers)
        gpg_data = _fetch_raw(gpg_url, headers)
        if release_data is not None and gpg_data is not None:
            return _verify_detached(release_data, gpg_data, keyring)

        print("Warning: No GPG verification data available (InRelease or Release.gpg)",
              file=sys.stderr)
        return False

    def download_deb(self, filename: str, dest: Path, label: str = "") -> Path:
        url = self._deb_url(filename)
        headers = {"User-Agent": "Tanuki/0.2.0"}
        dest.mkdir(parents=True, exist_ok=True)
        local_name = dest / Path(filename).name
        _download(url, local_name, headers, label=label)
        return local_name



def _fetch_decompressed(url: str, headers: Dict[str, str]) -> Optional[str]:
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
        if url.endswith(".gz"):
            return gzip.decompress(data).decode("utf-8", errors="replace")
        elif url.endswith(".xz"):
            return lzma.decompress(data).decode("utf-8", errors="replace")
        else:
            return data.decode("utf-8", errors="replace")
    except Exception:
        return None



def _fmt_size(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if abs(n) < 1024:
            return f"{n:5.1f} {unit}"
        n /= 1024
    return f"{n:5.1f} T"


def _fmt_speed(bytes_per_sec: float) -> str:
    return _fmt_size(bytes_per_sec) + "/s"


def _download(url: str, dest: Path, headers: Dict[str, str], label: str = ""):
    tty = sys.stdout.isatty()
    req = Request(url, headers=headers)
    bar_width = 25

    with urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        with open(dest, "wb") as f:
            downloaded = 0
            start = time.time()
            last_draw = 0.0

            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

                if tty and label and total > 0:
                    now = time.time()
                    if now - last_draw < 0.12 and downloaded < total:
                        continue
                    last_draw = now

                    pct = downloaded / total
                    elapsed = max(now - start, 0.001)
                    speed = downloaded / elapsed if elapsed > 0 else 0

                    filled = int(bar_width * pct)
                    if pct >= 1:
                        bar = "[" + "=" * bar_width + "]"
                        eta_str = "00:00"
                    else:
                        bar = "[" + "=" * filled + ">" + " " * (bar_width - filled - 1) + "]"
                        remaining = (total - downloaded) / speed if speed > 0 else 0
                        eta_m, eta_s = int(remaining // 60), int(remaining % 60)
                        eta_str = f"{eta_m:02d}:{eta_s:02d}"

                    line = (
                        f"\r  {label:<24s} {bar} {pct*100:3.0f}%  "
                        f"{_fmt_speed(speed)}  "
                        f"{_fmt_size(downloaded)}/{_fmt_size(total)}  "
                        f"{eta_str}"
                    )

                    term_width = shutil.get_terminal_size((80, 24)).columns
                    if len(line) > term_width:
                        line = line[:term_width - 1]
                    else:
                        line = line.ljust(term_width)

                    sys.stdout.write(line)
                    sys.stdout.flush()

        if tty and label:
            sys.stdout.write("\n")
            sys.stdout.flush()

    if total and downloaded != total:
        raise RuntimeError(f"Download incomplete: {downloaded}/{total} bytes")
    elif not total and downloaded == 0:
        raise RuntimeError(f"Download empty or failed: {dest.name}")



def verify_checksum(path: Path, expected: str, algo: str = "sha256") -> bool:
    if not expected:
        return True
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest() == expected



def _fetch_raw(url: str, headers: Dict[str, str]) -> Optional[bytes]:
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception:
        return None


def _verify_inrelease(data: bytes, keyring: Optional[Path] = None) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
        tf.write(data)
        tf.flush()
        try:
            return _run_gpgv(tf.name, keyring)
        finally:
            os.unlink(tf.name)


def _verify_detached(release: bytes, gpg: bytes, keyring: Optional[Path] = None) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as rf:
        rf.write(release)
        rf.flush()
        gpg_path = rf.name + ".sig"
        try:
            with open(gpg_path, "wb") as gf:
                gf.write(gpg)
            return _run_gpgv(rf.name, keyring, gpg_path)
        finally:
            os.unlink(rf.name)
            if os.path.exists(gpg_path):
                os.unlink(gpg_path)


def _run_gpgv(data_path: str, keyring: Optional[Path] = None,
              sig_path: Optional[str] = None) -> bool:
    known_keyrings = [
        "/usr/share/keyrings/debian-archive-keyring.gpg",
        "/usr/share/keyrings/debian-archive-removed-keys.gpg",
        "/etc/apt/trusted.gpg",
        "/etc/apt/trusted.gpg.d",
        "/etc/pacman.d/gnupg/pubring.gpg",
        "/usr/share/pacman/keyrings/",
        "/etc/pki/rpm-gpg/",
        "/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-36",
        "/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-37",
        "/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-38",
        "/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-39",
        "/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-40",
        "/usr/lib/rpm/gnupg/keys/",
    ]
    if keyring is not None:
        known_keyrings.insert(0, str(keyring))

    keyring_args = []
    for kr in known_keyrings:
        if os.path.isdir(kr):
            for fname in sorted(os.listdir(kr)):
                fpath = os.path.join(kr, fname)
                if fpath.endswith(".gpg") or fpath.endswith(".asc"):
                    keyring_args.extend(["--keyring", fpath])
        elif os.path.isfile(kr):
            keyring_args.extend(["--keyring", kr])

    cmd = ["gpgv", "--ignore-time-conflict"]
    if sig_path:
        cmd.extend([sig_path, data_path])
    else:
        cmd.append(data_path)

    try:
        proc = subprocess.run(
            cmd + keyring_args,
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not keyring_args:
        try:
            gpg_cmd = ["gpg", "--verify"]
            sig = sig_path or data_path
            if sig_path:
                gpg_cmd.extend([sig_path, data_path])
            else:
                gpg_cmd.append(data_path)
            proc = subprocess.run(
                gpg_cmd,
                capture_output=True, text=True, timeout=30,
            )
            return proc.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return False
