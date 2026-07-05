import struct
import io
import os
import tarfile
import lzma
import gzip
import subprocess
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, List, BinaryIO


AR_MAGIC = b"!<arch>\n"
AR_HEADER_FMT = "<16s12s6s6s8s10s2s"
AR_HEADER_SIZE = 60


@dataclass
class ArMember:
    name: str
    size: int
    offset: int


class ArReader:

    def __init__(self, fp: BinaryIO):
        magic = fp.read(8)
        if magic != AR_MAGIC:
            raise ValueError("Not a valid ar archive")
        self._fp = fp
        self._members: Dict[str, ArMember] = {}
        self._scan()

    def _scan(self):
        while True:
            header = self._fp.read(AR_HEADER_SIZE)
            if len(header) < AR_HEADER_SIZE:
                break
            name_raw, _, _, _, _, size_raw, magic = struct.unpack(
                AR_HEADER_FMT, header
            )
            if magic != b"`\n":
                break
            name = name_raw.rstrip(b" ").rstrip(b"/").decode("ascii", errors="replace")
            try:
                size = int(size_raw.strip())
            except ValueError:
                break
            offset = self._fp.tell()
            m = ArMember(name=name, size=size, offset=offset)
            self._members[name] = m
            skip = size + (size % 2)
            self._fp.seek(skip, 1)

    def open(self, name: str) -> BinaryIO:
        m = self._members.get(name)
        if m is None:
            raise KeyError(f"Member '{name}' not found in archive")
        self._fp.seek(m.offset)
        return io.BytesIO(self._fp.read(m.size))

    def list(self) -> List[str]:
        return list(self._members)


@dataclass
class ControlInfo:
    package: str = ""
    version: str = ""
    architecture: str = ""
    maintainer: str = ""
    description: str = ""
    section: str = ""
    priority: str = "optional"
    installed_size: int = 0
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


class DebPackage:

    def __init__(self, path: Path):
        self.path = path
        self._control: Optional[ControlInfo] = None
        self._scripts: Dict[str, str] = {}
        self._file_list: List[str] = []
        self._conffiles: List[str] = []
        self._triggers: List[str] = []
        self._read()

    def _read(self):
        with open(self.path, "rb") as f:
            ar = ArReader(f)

            for member_name in ar.list():
                if member_name == "control.tar.xz":
                    self._extract_control(ar.open("control.tar.xz"), "xz")
                elif member_name == "control.tar.gz":
                    self._extract_control(ar.open("control.tar.gz"), "gz")
                elif member_name == "control.tar.zst":
                    self._extract_control(ar.open("control.tar.zst"), "zst")
                elif member_name == "data.tar.xz":
                    self._extract_files(ar.open("data.tar.xz"), "xz")
                elif member_name == "data.tar.gz":
                    self._extract_files(ar.open("data.tar.gz"), "gz")
                elif member_name == "data.tar.zst":
                    self._extract_files(ar.open("data.tar.zst"), "zst")

    @staticmethod
    def _zstd_stream() -> Optional[Callable[[BinaryIO], BinaryIO]]:
        try:
            import zstandard
            def _read_zstd(stream: BinaryIO) -> BinaryIO:
                dctx = zstandard.ZstdDecompressor()
                return io.BufferedReader(dctx.stream_reader(stream))
            return _read_zstd
        except ImportError:
            pass
        try:
            import pyzstd
            def _read_zstd(stream: BinaryIO) -> BinaryIO:
                raw = stream.read()
                return io.BytesIO(pyzstd.decompress(raw))
            return _read_zstd
        except ImportError:
            pass
        return None

    def _open_tar(self, data: BinaryIO, compression: str) -> tarfile.TarFile:
        try:
            if compression == "xz":
                return tarfile.open(fileobj=data, mode="r:xz")
            elif compression == "gz":
                return tarfile.open(fileobj=data, mode="r:gz")
            elif compression == "zst":
                zstd_fn = self._zstd_stream()
                if zstd_fn:
                    return tarfile.open(fileobj=zstd_fn(data), mode="r:")
                proc = subprocess.run(
                    ["zstd", "-d", "-c"],
                    input=data.read(), capture_output=True, timeout=120,
                )
                if proc.returncode != 0:
                    raise RuntimeError("zstd decompression failed")
                return tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:")
            else:
                return tarfile.open(fileobj=data, mode="r:")
        except tarfile.ReadError as e:
            raise RuntimeError(f"Failed to open tar archive: {e}")

    @staticmethod
    def _safe_path(dest: Path, user_path: str) -> Path:
        clean = user_path.lstrip("/")
        full = (dest / clean).resolve()
        dest_resolved = dest.resolve()
        try:
            full.relative_to(dest_resolved)
        except ValueError:
            raise RuntimeError(f"Path traversal blocked: {user_path}")
        return full

    @staticmethod
    def _safe_symlink_target(target: str, symlink_path: Path, dest: Path) -> str:
        dest_resolved = dest.resolve()
        if target.startswith("/"):
            resolved = Path(target).resolve()
            try:
                resolved.relative_to(dest_resolved)
            except ValueError:
                raise RuntimeError(f"Symlink target escapes root: {target}")
            return target
        parent_resolved = symlink_path.parent.resolve()
        joined = (parent_resolved / target).resolve()
        try:
            joined.relative_to(dest_resolved)
        except ValueError:
            raise RuntimeError(f"Relative symlink escapes root: {target}")
        return target

    @staticmethod
    def _safe_open(path: Path) -> BinaryIO:
        path.unlink(missing_ok=True)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            return os.fdopen(fd, "wb")
        except FileExistsError:
            path.unlink(missing_ok=True)
            return open(path, "wb")

    def _extract_control(self, data: BinaryIO, compression: str):
        tf = self._open_tar(data, compression)
        text = ""
        scripts = {}
        conffiles = []
        triggers = []

        for member in tf.getmembers():
            name = member.name.lstrip("./")
            if name == "control":
                f = tf.extractfile(member)
                if f:
                    text = f.read().decode("utf-8", errors="replace")
            elif name in ("preinst", "postinst", "prerm", "postrm"):
                f = tf.extractfile(member)
                if f:
                    content = f.read().decode("utf-8", errors="replace")
                    scripts[name] = content
            elif name == "conffiles":
                f = tf.extractfile(member)
                if f:
                    content = f.read().decode("utf-8", errors="replace")
                    conffiles = [line.strip() for line in content.split("\n") if line.strip()]
            elif name == "triggers":
                f = tf.extractfile(member)
                if f:
                    content = f.read().decode("utf-8", errors="replace")
                    triggers = [line.strip() for line in content.split("\n") if line.strip()]

        tf.close()
        self._scripts = scripts
        self._conffiles = conffiles
        self._triggers = triggers
        self._control = parse_control(text)

    def _extract_files(self, data: BinaryIO, compression: str):
        tf = self._open_tar(data, compression)
        self._file_list = [
            member.name.lstrip("./")
            for member in tf.getmembers()
            if member.isfile() or member.islnk() or member.issym()
        ]
        tf.close()

    @property
    def control(self) -> ControlInfo:
        if self._control is None:
            raise RuntimeError("Package not read")
        return self._control

    @property
    def scripts(self) -> Dict[str, str]:
        return self._scripts

    @property
    def conffiles(self) -> List[str]:
        return self._conffiles

    @property
    def files(self) -> List[str]:
        return self._file_list

    @property
    def triggers(self) -> List[str]:
        return getattr(self, "_triggers", [])

    def extract_data(self, dest: Path, path_rewrite: Optional[Callable[[str], str]] = None):
        with open(self.path, "rb") as f:
            ar = ArReader(f)
            for member_name in ar.list():
                if member_name.startswith("data.tar"):
                    if member_name.endswith(".xz"):
                        comp = "xz"
                    elif member_name.endswith(".gz"):
                        comp = "gz"
                    elif member_name.endswith(".zst"):
                        comp = "zst"
                    else:
                        continue
                    tf = self._open_tar(ar.open(member_name), comp)

                    if path_rewrite:
                        hardlinks: Dict[str, str] = {}
                        for member in tf.getmembers():
                            name = member.name.lstrip("./")
                            if not name:
                                continue
                            new_name = path_rewrite(name)
                            dest_path = self._safe_path(dest, new_name)

                            if member.issym():
                                target = member.linkname
                                if target.startswith("/"):
                                    rewritten = path_rewrite(target.lstrip("/"))
                                    target = "/" + rewritten if not rewritten.startswith("/") else rewritten
                                dest_path.parent.mkdir(parents=True, exist_ok=True)
                                self._safe_symlink_target(target, dest_path, dest)
                                dest_path.unlink(missing_ok=True)
                                dest_path.symlink_to(target)
                            elif member.islnk():
                                clean_link = member.linkname.lstrip("./")
                                hardlinks[name] = clean_link
                            elif member.isfile():
                                dest_path.parent.mkdir(parents=True, exist_ok=True)
                                src = tf.extractfile(member)
                                if src:
                                    with src, self._safe_open(dest_path) as dst:
                                        shutil.copyfileobj(src, dst)
                                os.chmod(dest_path, member.mode)
                            elif member.isdir():
                                dest_path.mkdir(parents=True, exist_ok=True)

                        for name, linkname in hardlinks.items():
                            new_name = path_rewrite(name)
                            new_linkname = path_rewrite(linkname)
                            dest_path = self._safe_path(dest, new_name)
                            target_path = self._safe_path(dest, new_linkname)
                            dest_path.parent.mkdir(parents=True, exist_ok=True)
                            dest_path.unlink(missing_ok=True)
                            os.link(str(target_path), str(dest_path))
                    else:
                        extract_kwargs = dict(path=str(dest), numeric_owner=True)
                        try:
                            tf.extractall(filter="data", **extract_kwargs)
                        except TypeError:
                            tf.extractall(**extract_kwargs)

                    tf.close()
                    return
        raise RuntimeError("No data archive found in package")


MULTIARCH_TRIPLES = [
    "x86_64-linux-gnu", "aarch64-linux-gnu", "arm-linux-gnueabihf",
    "arm-linux-gnueabi", "i386-linux-gnu", "mips-linux-gnu",
    "mips64el-linux-gnuabi64", "mipsel-linux-gnu", "powerpc-linux-gnu",
    "powerpc64le-linux-gnu", "riscv64-linux-gnu", "s390x-linux-gnu",
]


def rewrite_file_contents(root: Path, file_path: str, path_rewrite: Optional[Callable[[str], str]]):
    full = root / file_path.lstrip("/")
    if not full.is_file():
        return
    try:
        raw = full.read_bytes()
    except Exception:
        return
    if not raw:
        return
    ext = full.suffix
    if ext in (".so", ".so.", ".a", ".o", ".pyc", ".png", ".jpg", ".gz"):
        return
    is_text = True
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        is_text = False
    if not is_text and ext not in (".pc",):
        return
    text = raw.decode("utf-8", errors="replace")
    new_text = text
    for triple in MULTIARCH_TRIPLES:
        old = f"/usr/lib/{triple}"
        new = "/usr/lib"
        new_text = new_text.replace(old, new)
    if new_text != text:
        try:
            full.write_bytes(new_text.encode("utf-8"))
        except Exception:
            pass


def rewrite_shebangs(root: Path, file_path: str):
    full = root / file_path.lstrip("/")
    if not full.is_file():
        return
    try:
        raw = full.read_bytes()
    except Exception:
        return
    if not raw.startswith(b"#!"):
        return
    text = raw.decode("utf-8", errors="replace")
    first_line = text.split("\n")[0]
    new_line = first_line
    subs = [
        ("/usr/bin/python3", shutil.which("python3") or "/usr/bin/python3"),
        ("/usr/bin/python", shutil.which("python3") or "/usr/bin/python"),
        ("/usr/bin/bash", shutil.which("bash") or "/usr/bin/bash"),
        ("/bin/bash", shutil.which("bash") or "/bin/bash"),
        ("/bin/sh", shutil.which("sh") or "/bin/sh"),
    ]
    for old, new_path in subs:
        if old in new_line and old != new_path:
            new_line = new_line.replace(old, new_path)
    if new_line != first_line:
        try:
            full.write_bytes(("\n".join([new_line] + text.split("\n")[1:])).encode("utf-8"))
        except Exception:
            pass


def parse_control(text: str) -> ControlInfo:
    info = ControlInfo(raw={})
    current_key = None
    current_value = []

    for line in text.split("\n"):
        if not line:
            continue
        if line[0] in (" ", "\t") and current_key:
            current_value.append(line.strip())
        elif ":" in line:
            if current_key is not None:
                val = " ".join(v for v in current_value if v).strip()
                info.raw[current_key] = val
            key, _, value = line.partition(":")
            current_key = key.strip()
            current_value = [value.strip()]

    if current_key is not None:
        val = " ".join(v for v in current_value if v).strip()
        info.raw[current_key] = val

    for key in list(info.raw.keys()):
        lkey = key.lower()
        if lkey != key:
            info.raw[lkey] = info.raw[key]

    _map = {
        "package": "package",
        "version": "version",
        "architecture": "architecture",
        "maintainer": "maintainer",
        "description": "description",
        "section": "section",
        "priority": "priority",
        "installed-size": "installed_size",
        "depends": "depends",
        "pre-depends": "pre_depends",
        "recommends": "recommends",
        "suggests": "suggests",
        "conflicts": "conflicts",
        "replaces": "replaces",
        "provides": "provides",
        "breaks": "breaks",
        "enhances": "enhances",
        "essential": "essential",
        "homepage": "homepage",
    }
    for raw_key, attr in _map.items():
        if raw_key in info.raw:
            val = info.raw[raw_key]
            if attr == "installed_size":
                try:
                    val = int(val)
                except ValueError:
                    val = 0
            setattr(info, attr, val)

    return info
