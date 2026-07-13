import sys, time, shutil

VERSION = "0.2.0"

_COLORS = {
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "cyan": "\033[36m", "bold": "\033[1m",
    "reset": "\033[0m",
}

_use_color = sys.stdout.isatty()


def _color(code, text):
    if not _use_color:
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
    print(f"    {_color('green', 'count, c')}                         Show installed package count")
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


def print_success(msg):
    print(_color("green", f"✓ {msg}"))


def print_error(msg):
    print(_color("red", f"✗ {msg}"), file=sys.stderr)


def print_info(msg):
    print(_color("blue", f"• {msg}"))


def print_warning(msg):
    print(_color("yellow", f"! {msg}"))


def print_table(headers, rows):
    if not rows:
        return
    all_rows = [headers] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]
    sep = "  "
    hline = sep.join(h.ljust(w) for h, w in zip(headers, widths))
    print(_color("bold", hline))
    print("-" * len(hline))
    for row in rows:
        print(sep.join(str(c).ljust(w) for c, w in zip(row, widths)))


def prompt_yes_no(question, default=True):
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        a = input(_color("bold", question) + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not a:
        return default
    return a in ("y", "yes")


_tty = sys.stdout.isatty()


def term_w(fallback=80):
    return shutil.get_terminal_size((fallback, 24)).columns


def pretty_size(n):
    for unit in ("B", "Ki", "Mi", "Gi"):
        if abs(n) < 1024:
            return f"{n:5.1f} {unit}"
        n /= 1024
    return f"{n:5.1f} Gi"


def pretty_speed(bps):
    return pretty_size(bps) + "/s"


def print_stage(label):
    print(f"{_color('cyan', '[')} {_color('bold', label)} {_color('cyan', ']')}")


def print_sep(char="─", width=None):
    tw = term_w() if width is None else width
    print(char * min(tw, 80))


def print_ruler(label=""):
    tw = term_w()
    lbl = f" {label} " if label else ""
    avail = tw - len(lbl) - 2
    left = avail // 2
    right = avail - left
    print(f"{'─' * left}{lbl}{'─' * right}")


class ProgressBar:
    def __init__(self, prefix="", total=0, bar_width=25, counter=""):
        self.prefix = prefix
        self.total = total
        self.bar_width = bar_width
        self.counter = counter
        self.done = 0
        self.start = 0.0
        self.last = 0.0
        self._finished = False

    def start_timer(self):
        self.start = time.time()

    def update(self, n):
        self.done += n
        if _tty:
            self._draw()

    def _draw(self):
        now = time.time()
        if now - self.last < 0.12 and self.done < self.total:
            return
        self.last = now

        elapsed = max(now - self.start, 0.001)
        speed = self.done / elapsed if elapsed > 0 else 0
        pct = self.done / self.total if self.total > 0 else 0
        filled = int(self.bar_width * pct)

        if pct >= 1:
            bar = "[" + "=" * self.bar_width + "]"
            eta = "00:00"
        else:
            bar = "[" + "=" * filled + ">" + " " * (self.bar_width - filled - 1) + "]"
            rem = (self.total - self.done) / speed if speed > 0 else 0
            m, s = int(rem // 60), int(rem % 60)
            eta = f"{m:02d}:{s:02d}"

        line = (f"\r  {self.counter}{self.prefix:<20s} {bar} {pct*100:3.0f}%  "
                f"{pretty_speed(speed)}  "
                f"{pretty_size(self.done)}/{pretty_size(self.total)}  {eta}")
        tw = term_w()
        line = line[:tw - 1] if len(line) > tw else line.ljust(tw)
        sys.stdout.write(line)
        sys.stdout.flush()

    def finish(self, msg=""):
        if not _tty or self._finished:
            return
        if self.total and self.done >= self.total:
            line = f"\r  {self.counter}{self.prefix:<20s} {'[' + '=' * self.bar_width + ']':27s}"
            tw = term_w()
            line = line[:tw - 1] if len(line) > tw else line.ljust(tw)
            sys.stdout.write(line)
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._finished = True


class Spinner:
    CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, prefix=""):
        self.prefix = prefix
        self._i = 0
        self._started = False

    def start(self):
        self._started = True
        self._i = 0
        if _tty:
            self._draw()

    def tick(self):
        if not self._started or not _tty:
            return
        self._i += 1
        self._draw()

    def _draw(self):
        ch = self.CHARS[self._i % len(self.CHARS)]
        line = f"\r  {ch} {self.prefix}"
        tw = term_w()
        line = line[:tw - 1] if len(line) > tw else line.ljust(tw)
        sys.stdout.write(line)
        sys.stdout.flush()

    def stop(self, msg="done"):
        if not _tty:
            return
        line = f"\r  {_color('green', '✓')} {self.prefix}{msg}"
        tw = term_w()
        line = line[:tw - 1] if len(line) > tw else line.ljust(tw)
        sys.stdout.write(line)
        sys.stdout.write("\n")
        sys.stdout.flush()


def print_table_box(title, headers, rows, footers=None):
    if not rows:
        return
    all_rows = [[str(c) for c in r] for r in rows]
    headers = [str(h) for h in headers]
    widths = [max(len(r[i]) for r in all_rows + [headers]) for i in range(len(headers))]
    sep = "  "

    print_ruler(title)
    print(_color("bold", sep.join(h.ljust(w) for h, w in zip(headers, widths))))
    print_ruler()
    for row in all_rows:
        print(sep.join(c.ljust(w) for c, w in zip(row, widths)))
    print_ruler()
    if footers:
        for label, val in footers:
            print(f"  {label:<40s} {val}")
