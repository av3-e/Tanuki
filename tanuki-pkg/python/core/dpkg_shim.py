import os
import sys
import tempfile
import shutil
import json
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

    for name in ("dpkg-query", "dpkg-deb", "dpkg-divert", "dpkg-trigger", "start-stop-daemon"):
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

    if os.path.basename(sys.argv[0]) == "start-stop-daemon":
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
        if len(args) > 1:
            print(f"dpkg-shim: --configure ignored for {args[1]}", file=sys.stderr)
        return 0

    if args[0] in ("--unpack", "--audit"):
        if len(args) > 1:
            print(f"dpkg-shim: dpkg --unpack stubbed", file=sys.stderr)
        return 0

    if args[0] in ("--add", "--remove", "--list", "--truename") and os.path.basename(sys.argv[0]) == "dpkg-divert":
        return _cmd_divert(args)

    if args[0] == "--no-await":
        if len(args) > 1:
            return _cmd_trigger(args[1:])
        return 0

    if args[0].startswith("--no-triggers") or args[0].startswith("--force-"):
        rest = [a for a in args if not a.startswith("--")]
        if rest:
            print(f"dpkg-shim: stubbed {' '.join(rest)}", file=sys.stderr)
            return 0
        return 0

    if args[0].startswith("-"):
        print(f"dpkg-shim: stubbed dpkg {' '.join(args)}", file=sys.stderr)
        return 0

    return _try_install_or_remove(args)


def _try_install_or_remove(args: list) -> int:
    if args:
        print(f"dpkg-shim: ignoring dpkg {' '.join(args)}", file=sys.stderr)
    return 0


def _get_divert_db():
    db_dir = Path(os.environ.get("TANUKI_DB_DIR", "/var/lib/tanuki"))
    div_file = db_dir / "diversions.json"
    if div_file.exists():
        try:
            return json.loads(div_file.read_text())
        except Exception:
            return []
    return []


def _save_divert_db(diversions):
    db_dir = Path(os.environ.get("TANUKI_DB_DIR", "/var/lib/tanuki"))
    div_file = db_dir / "diversions.json"
    try:
        div_file.write_text(json.dumps(diversions, indent=1))
    except Exception:
        pass


def _cmd_divert(args: list) -> int:
    action = args[0]
    rest = args[1:]
    diversions = _get_divert_db()
    if action == "--list":
        for d in diversions:
            print(f"diversion of {d['orig']} to {d['diverted']} by {d['package']}")
        return 0
    elif action == "--truename":
        if rest:
            for p in rest:
                found = [d for d in diversions if d['orig'] == p]
                print(found[0]['diverted'] if found else p)
        return 0
    elif action == "--add":
        pkg = None
        divert_to = None
        rename = False
        i = 0
        while i < len(rest):
            if rest[i] == "--divert":
                i += 1
                if i < len(rest):
                    divert_to = rest[i]
            elif rest[i] == "--package":
                i += 1
                if i < len(rest):
                    pkg = rest[i]
            elif rest[i] == "--rename":
                rename = True
            elif not rest[i].startswith("--"):
                orig = rest[i]
                if divert_to:
                    diversions = [d for d in diversions if d['orig'] != orig]
                    diversions.append({"orig": orig, "diverted": divert_to, "package": pkg or "unknown", "rename": rename})
                    if rename and os.path.exists(orig) and not os.path.exists(divert_to):
                        try:
                            os.rename(orig, divert_to)
                        except Exception:
                            pass
                _save_divert_db(diversions)
                return 0
            i += 1
        return 0
    elif action == "--remove":
        if rest:
            orig = rest[-1] if not rest[-1].startswith("--") else None
            if orig:
                removed = [d for d in diversions if d['orig'] != orig]
                old = next((d for d in diversions if d['orig'] == orig), None)
                if old and old.get("rename") and os.path.exists(old['diverted']) and not os.path.exists(orig):
                    try:
                        os.rename(old['diverted'], orig)
                    except Exception:
                        pass
                _save_divert_db(removed)
        return 0
    return 0


_trigger_queue: list = []


def _cmd_trigger(args: list) -> int:
    if not args:
        for t in _trigger_queue:
            print(t)
        return 0
    for a in args:
        if not a.startswith("-"):
            _trigger_queue.append(a)
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
    if not db:
        return 0
    files = db.get_files(args[0])
    for f in files:
        print("/" + f.lstrip("/"))
    return 0


def _cmd_search_files(args: list) -> int:
    db = _get_db()
    if not db:
        return 0
    for arg in args:
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
            print(f"Architecture: {pkg.architecture}")
            if pkg.depends:
                print(f"Depends: {pkg.depends}")
            if pkg.provides:
                print(f"Provides: {pkg.provides}")
        else:
            print(f"Package: {name}")
            print(f"Status: install ok not-installed")
    return 0


def _cmd_query(args: list) -> int:
    db = _get_db()
    fmt = "default"
    if args and args[0] == "-f":
        if len(args) >= 3:
            fmt = args[1]
            args = args[2:]
        elif len(args) == 2:
            fmt = args[1]
            args = []
        else:
            return 1
    if not db:
        for name in args:
            print(f"{name}\t(none)")
        return 0
    for name in args:
        pkg = db.get_package(name)
        if pkg and fmt != "default":
            line = fmt
            subs = [("Package", pkg.name), ("Version", pkg.version), ("Architecture", pkg.architecture), ("db-fsys:Version", pkg.version), ("Status", "install ok installed")]
            for k, v in subs:
                line = line.replace("${" + k + "}", v)
            print(line, end="")
        elif pkg:
            print(f"{pkg.name}\t{pkg.version}")
        else:
            print(f"{name}\t(none)")
    return 0
