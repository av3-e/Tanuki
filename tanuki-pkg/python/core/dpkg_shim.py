import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Optional


def setup_shim(db_path: Path, arch: str) -> Path:
    script_dir = Path(tempfile.mkdtemp(prefix="tanuki-shim-"))
    bin_dir = script_dir / "bin"
    bin_dir.mkdir(parents=True)

    shim_code = f'''#!/usr/bin/env python3
import sys, os
sys.path = {sys.path!r}
os.environ["TANUKI_DB_DIR"] = {str(db_path)!r}
os.environ["TANUKI_ARCH"] = {arch!r}
from core.dpkg_shim import shim_main
sys.exit(shim_main())
'''
    dpkg_path = bin_dir / "dpkg"
    dpkg_path.write_text(shim_code)
    dpkg_path.chmod(0o755)

    for name in ("dpkg-query", "dpkg-deb", "dpkg-divert"):
        (bin_dir / name).symlink_to("dpkg")

    return script_dir


def cleanup_shim(script_dir: Path):
    shutil.rmtree(str(script_dir), ignore_errors=True)


COMMANDS = {
    "--compare-versions": "compare_versions",
    "--compare-version": "compare_versions",
    "--print-architecture": "print_architecture",
    "--print-arch": "print_architecture",
    "-L": "list_files",
    "--listfiles": "list_files",
    "-S": "search_files",
    "--search": "search_files",
    "-s": "status",
    "--status": "status",
    "-W": "query",
    "--show": "query",
    "--version": "version",
    "--configure": "configure",
    "--no-triggers": "noop_flag",
    "--unpack": "unpack",
    "--force-depends": "noop_flag",
    "--force-confnew": "noop_flag",
    "--force-confold": "noop_flag",
    "--force-confdef": "noop_flag",
    "--force-confmiss": "noop_flag",
    "--force-overwrite": "noop_flag",
    "--force-all": "noop_flag",
    "--audit": "noop",
    "--assert-*": "noop",
}


def shim_main() -> int:
    args = sys.argv[1:]
    if not args:
        return 0

    if args[0] == "--compare-versions":
        return _cmd_compare_versions(args[1:])

    if args[0] in ("--print-architecture", "--print-arch"):
        return _cmd_print_architecture()

    if args[0] in ("-L", "--listfiles"):
        return _cmd_list_files(args[1:])

    if args[0] in ("-S", "--search"):
        return _cmd_search_files(args[1:])

    if args[0] in ("-s", "--status"):
        return _cmd_status(args[1:])

    if args[0] in ("-W", "--show"):
        return _cmd_query(args[1:])

    if args[0] == "--version":
        print("tanuki dpkg-shim 0.1")
        return 0

    if args[0] == "--configure":
        return 0

    if args[0] in ("--unpack", "--audit"):
        return 0

    if args[0].startswith("--no-triggers") or args[0].startswith("--force-"):
        rest = [a for a in args if not a.startswith("--")]
        if rest:
            if args[0].startswith("--force-"):
                from core.package import DebPackage
            pass
        if rest:
            return _try_install_or_remove(rest)
        return 0

    return _try_install_or_remove(args)


def _try_install_or_remove(args: list) -> int:
    return 0


def _cmd_compare_versions(args: list) -> int:
    if len(args) < 3:
        print("dpkg --compare-versions: expected <v1> <op> <v2>", file=sys.stderr)
        return 1
    v1, op, v2 = args[0], args[1], args[2]
    from core.dependency import _compare_versions
    cmp = _compare_versions(v1, v2)
    result = False
    if op in ("lt", "le", "eq", "ne", "ge", "gt"):
        result = {
            "lt": cmp < 0,
            "le": cmp <= 0,
            "eq": cmp == 0,
            "ne": cmp != 0,
            "ge": cmp >= 0,
            "gt": cmp > 0,
        }.get(op, False)
    elif op == "<<":
        result = cmp < 0
    elif op == ">>":
        result = cmp > 0
    elif op == "=":
        result = cmp == 0
    elif op == ">=":
        result = cmp >= 0
    elif op == "<=":
        result = cmp <= 0
    return 0 if result else 1


def _cmd_print_architecture() -> int:
    from core.host_detect import detect_architecture
    print(detect_architecture())
    return 0


def _get_db() -> Optional[object]:
    from core.database import PackageDatabase
    db_dir = Path(os.environ.get("TANUKI_DB_DIR", "/var/lib/tanuki"))
    if db_dir.exists():
        return PackageDatabase(db_dir)
    return None


def _cmd_list_files(args: list) -> int:
    if not args:
        print("dpkg: --listfiles requires a package name", file=sys.stderr)
        return 1
    db = _get_db()
    if db:
        files = db.get_files(args[0])
        for f in files:
            print("/" + f.lstrip("/"))
    return 0


def _cmd_search_files(args: list) -> int:
    for arg in args:
        db = _get_db()
        if db:
            owner = db.file_owned_by(arg)
            if owner:
                print(f"{owner}: {arg}")
    return 0


def _cmd_status(args: list) -> int:
    db = _get_db()
    for name in args:
        pkg = db.get_package(name) if db else None
        if pkg:
            print(f"Package: {pkg.name}")
            print(f"Status: install ok installed")
            print(f"Version: {pkg.version}")
        else:
            print(f"Package: {name}")
            print(f"Status: install ok not-installed")
    return 0


def _cmd_query(args: list) -> int:
    db = _get_db()
    fmt = "default"
    if args and args[0] == "-f":
        args = args[2:]
    for name in args:
        pkg = db.get_package(name) if db else None
        if pkg:
            print(f"{pkg.name}\t{pkg.version}")
        else:
            print(f"{name}\t(none)")
    return 0
