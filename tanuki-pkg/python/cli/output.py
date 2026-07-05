import sys
import shutil

VERSION = "0.2.0"

_COLORS = {
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}

_ENABLE_COLOR = sys.stdout.isatty()


def _color(code: str, text: str) -> str:
    if not _ENABLE_COLOR:
        return text
    return f"{_COLORS[code]}{text}{_COLORS['reset']}"


def print_version():
    print(f"Tanuki package manager v{VERSION}")


def print_help():
    print(f"{_color('bold', 'Tanuki')} — a {_color('cyan', '.deb-compatible')} package manager")
    print()
    print(f"  {_color('bold', 'Usage:')} tanuki [command|flag] [args...]")
    print()
    print(f"  {_color('bold', 'Commands:')}")
    print(f"    {_color('green', 'install, i')}     <pkg> [pkg...]   Install packages")
    print(f"    {_color('green', 'remove, rm')}     <pkg> [pkg...]   Remove packages")
    print(f"    {_color('green', 'update, up')}                      Update repository index")
    print(f"    {_color('green', 'upgrade, -U')}                     Upgrade installed packages")
    print(f"    {_color('green', 'list, ls')}     [--files] [pkg]    List packages (or files)")
    print(f"    {_color('green', 'files, fl')}    [pkg]              Show files of a package")
    print(f"    {_color('green', 'verify')}       [pkg]              Verify installed files")
    print(f"    {_color('green', 'undo, rollback')}                  Undo last install operation")
    print(f"    {_color('green', 'search')}        <query>           Search repositories")
    print(f"    {_color('green', 'info')}          <pkg>             Show package details")
    print(f"    {_color('green', 'provides')}      <virtual-pkg>     Show packages providing a virtual")
    print(f"    {_color('green', 'reinstall, re')} <pkg>             Reinstall a package")
    print(f"    {_color('green', 'purge, p')}      <pattern>         Purge packages by pattern")
    print(f"    {_color('green', 'clean')}                           Clear package cache")
    print(f"    {_color('green', 'autoclean')}                       Remove stale cached packages")
    print(f"    {_color('green', 'autoremove')}                      Remove orphaned auto-deps")
    print(f"    {_color('green', 'snapshot')}      [create|restore]  Manage snapshots")
    print(f"    {_color('green', 'init')}                            Initialize Tanuki")
    print()
    print(f"  {_color('bold', 'Install flags:')}")
    print(f"    {_color('yellow', '--dry-run')}         Preview without installing")
    print(f"    {_color('yellow', '--download-only')}    Download .debs only, skip install")
    print(f"    {_color('yellow', '--ignore-deps')}      Skip dependency resolution")
    print(f"    {_color('yellow', '--force')}            Ignore file conflicts")
    print(f"    {_color('yellow', '--with-recommends')}  Install recommended packages too")
    print(f"    {_color('yellow', '--root <path>')}      Install into alternate root")
    print()
    print(f"  {_color('bold', 'List flags:')}")
    print(f"    {_color('yellow', '--files')}            Show files instead of package list")
    print()
    print(f"  {_color('bold', 'General flags:')}")
    print(f"    {_color('yellow', '-h, --help')}         Show this help")
    print(f"    {_color('yellow', '-v, --version')}      Show version")
    print(f"    {_color('yellow', '-I, --install')}      Install packages")
    print(f"    {_color('yellow', '-u, --update')}       Update repositories")
    print(f"    {_color('yellow', '-r, --reinstall')}    Reinstall package")
    print(f"    {_color('yellow', '-p, --purge')}        Purge by pattern")
    print(f"    {_color('yellow', '-U, --upgrade')}      Upgrade packages")


def print_success(msg: str):
    print(_color("green", f"✓ {msg}"))


def print_error(msg: str):
    print(_color("red", f"✗ {msg}"), file=sys.stderr)


def print_info(msg: str):
    print(_color("blue", f"• {msg}"))


def print_warning(msg: str):
    print(_color("yellow", f"! {msg}"))


def print_table(headers: list, rows: list):
    if not rows:
        return
    all_rows = [headers] + [[str(c) for c in r] for r in rows]
    col_widths = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]
    sep = "  "
    header_line = sep.join(h.ljust(w) for h, w in zip(headers, col_widths))
    print(_color("bold", header_line))
    print("-" * len(header_line))
    for row in rows:
        line = sep.join(str(c).ljust(w) for c, w in zip(row, col_widths))
        print(line)


def prompt_yes_no(question: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(_color("bold", question) + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer:
        return default
    return answer in ("y", "yes")
