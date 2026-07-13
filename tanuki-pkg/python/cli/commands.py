import os
import sys
import json
import time
import fcntl
import shutil
import hashlib
import subprocess
import concurrent.futures
from pathlib import Path
from typing import List, Optional, Set, Dict
from datetime import datetime, timedelta

from .output import (
    print_table, print_success, print_error, print_info, print_warning,
    prompt_yes_no, print_stage, print_sep, print_ruler,
    Spinner, pretty_size,
)
from core.database import PackageDatabase, InstalledPackage
from core.dependency import DependencySolver, _compare_versions
from core.host_detect import (
    detect_host_packages, get_foreign_pm_files, FOREIGN_LOCKS,
    detect_distro, detect_architecture, get_distro_family,
    make_path_rewrite,
)
from core.repository import Repository, RepositoryIndex, RepoPackage, verify_checksum
from core.package import DebPackage, ControlInfo, rewrite_file_contents, rewrite_shebangs

TANUKI_ROOT = Path(os.environ.get(
    "TANUKI_ROOT",
    os.environ.get("TANUKI_DB_DIR", "/var/lib/tanuki"),
))
TANUKI_CACHE = Path(os.environ.get(
    "TANUKI_CACHE",
    os.environ.get("TANUKI_CACHE_DIR", "/var/cache/tanuki/archives"),
))


def _ensure_dir(path: Path, fallback: Path = None) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.access(path, os.W_OK):
            return path
    except PermissionError:
        pass
    if fallback is None:
        fallback = Path.home() / ".local/share/tanuki"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        if os.access(fallback, os.W_OK):
            return fallback
    except PermissionError:
        pass
    tmpfall = Path("/tmp/tanuki-db")
    tmpfall.mkdir(parents=True, exist_ok=True)
    return tmpfall


class Commands:
    def __init__(self, config: dict):
        self.config = config
        self.db_path = TANUKI_ROOT
        self.cache_path = TANUKI_CACHE
        self._setup()
        self.db = PackageDatabase(self.db_path)
        self.solver = DependencySolver(target_arch=self.config.get("arch", None))
        self.repo: Repository | None = None
        self._lock_fd = None
        self._lock_count = 0
        self._transaction_files: List[str] = []
        self._transaction_conffiles: List[tuple] = []

    def _setup(self):
        self.db_path = _ensure_dir(TANUKI_ROOT, Path.home() / ".local/share/tanuki")
        self.cache_path = _ensure_dir(TANUKI_CACHE, Path.home() / ".cache/tanuki/archives")
        _ensure_dir(self.db_path / "info", self.db_path / "info")

    def _check_system_locks(self):
        foreign = []
        for label, lock_paths in FOREIGN_LOCKS.items():
            for lock_path in lock_paths:
                if os.path.exists(lock_path):
                    foreign.append(label)
                    break
        if foreign:
            print_warning(f"System package managers appear active: {', '.join(foreign)}")
            print_info("Conflicts may occur. Proceed with caution.")
            if not prompt_yes_no("Continue anyway?"):
                sys.exit(1)

    def _acquire_lock(self):
        if self._lock_count > 0:
            self._lock_count += 1
            return
        self._lock_fd = open(self.db_path / "lock", "a+")
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            print_error("Another tanuki process is running (lock held)")
            sys.exit(1)
        self._lock_count = 1

    def _release_lock(self):
        if self._lock_count <= 1:
            if self._lock_fd:
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                except Exception as e:
                    print_warning(f"Failed to unlock: {e}")
                try:
                    self._lock_fd.close()
                except Exception as e:
                    print_warning(f"Failed to close lock fd: {e}")
                self._lock_fd = None
                self._lock_count = 0
        else:
            self._lock_count -= 1

    def _get_repo(self) -> Repository:
        if self.repo is None:
            mirror = self.config.get("mirror", "https://deb.debian.org/debian")
            suite = self.config.get("suite", "sid")
            components = self.config.get("components",
                                            ["main", "contrib", "non-free-firmware", "non-free"])
            architectures = self.config.get("architectures",
                                            [self.config.get("arch", "amd64")])
            self.repo = Repository(mirror, suite, components, architectures)
            cache_file = self.db_path / "repo-index.json"
            if cache_file.exists():
                self.repo.index = RepositoryIndex.load(cache_file)
        return self.repo

    def _require_root(self):
        if os.geteuid() != 0:
            print_error("This operation requires root privileges")
            sys.exit(1)

    def _resolve_root(self) -> Path:
        return Path(self.config.get("root", "/"))

    def _begin_transaction(self):
        self._transaction_files = []
        self._transaction_conffiles = []

    def _rollback_transaction(self, root: Path):
        for fpath in reversed(self._transaction_files):
            full = root / fpath.lstrip("/")
            try:
                if full.is_file() or full.is_symlink():
                    full.unlink(missing_ok=True)
            except Exception as e:
                print_warning(f"rollback: could not remove {fpath}: {e}")
        for orig, backup in self._transaction_conffiles:
            if backup and os.path.exists(backup):
                try:
                    shutil.copy2(backup, orig)
                    Path(backup).unlink(missing_ok=True)
                except Exception as e:
                    print_warning(f"rollback: could not restore {orig}: {e}")
        self._transaction_files = []
        self._transaction_conffiles = []

    def install(self, package_names: List[str], dry_run: bool = False,
                download_only: bool = False, ignore_deps: bool = False,
                force: bool = False, with_recommends: bool = False):
        self._require_root()
        if not package_names:
            print_error("Package name(s) required")
            return

        self._acquire_lock()
        try:
            self._check_system_locks()

            repo = self._get_repo()
            if not repo.index._packages:
                repo.update()

            snapshots_dir = self.db_path / "snapshots"
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            pre_snap = snapshots_dir / f"pre-{int(time.time())}.list"
            try:
                before = self.db.get_all_packages()
                pre_snap.write_text("\n".join(f"{p.name}\t{p.version}\t{p.architecture}" for p in before))
            except Exception as e:
                print_warning(f"could not save pre-install snapshot: {e}")

            print_stage("Running transaction check")
            print("  Detecting system packages...")
            host_pkgs = detect_host_packages(extra_lib_map=repo.lib_mapping or None)
            print(f"  Detected {len(host_pkgs)} host packages")
            if not force:
                print("  Checking foreign package managers...")
                foreign_files = get_foreign_pm_files()
                if foreign_files:
                    for pm in foreign_files:
                        print(f"    {pm}: {len(foreign_files[pm])} files tracked")
                else:
                    print("    none found")
            if host_pkgs:
                self.solver.load_installed(host_pkgs)
            installed_pkgs = self.db.get_all_packages()
            self.solver.load_installed(
                {p.name: p.version for p in installed_pkgs}
            )
            self.solver.load_installed_metadata(installed_pkgs)
            if host_pkgs:
                self.solver.register_host_metadata(host_pkgs, repo.index)
            self.solver.with_recommends = with_recommends

            for pkg_name in package_names:
                arch = None
                if ":" in pkg_name and not pkg_name.startswith(":"):
                    pkg_name, _, arch = pkg_name.partition(":")
                try:
                    self._install_one(pkg_name, repo, dry_run=dry_run,
                                       download_only=download_only, arch=arch,
                                       ignore_deps=ignore_deps, force=force,
                                       foreign_files=locals().get("foreign_files", {}),
                                       with_recommends=with_recommends)
                except Exception as e:
                    print_error(f"Failed to install {pkg_name}: {e}")
        finally:
            self._release_lock()

    def _install_one(self, name: str, repo: Repository, dry_run: bool = False,
                     download_only: bool = False, explicit: bool = True,
                     arch: Optional[str] = None, ignore_deps: bool = False,
                     force: bool = False, foreign_files: Dict[str, Set[str]] = None,
                     with_recommends: bool = False):
        if foreign_files is None:
            foreign_files = {}

        candidates = repo.index.get(name)
        if arch and candidates:
            candidates = [c for c in candidates if c.architecture == arch] or candidates
        if not candidates:
            print_error(f"Package '{name}' not found in repository")
            return
        pkg_info = candidates[0]

        conflicts = self.solver.check_conflicts(name, repo.index)
        if conflicts:
            print_error(f"Cannot install {name}: {'; '.join(conflicts)}")
            return

        if ignore_deps:
            to_install = [name]
            print_warning("Dependencies ignored -- package may not function")
        else:
            print_info(f"Resolving dependencies for {name}...")
            try:
                to_install = self.solver.resolve(name, repo.index)
            except RuntimeError as e:
                print_error(str(e))
                return

        if dry_run:
            print_info(f"Would install: {' '.join(to_install)}")
            return

        target_arch = self.config.get("arch", detect_architecture())

        stuff = {}
        for d in reversed(to_install):
            cand = repo.index.get(d)
            if not cand:
                print_error(f"Package '{d}' resolved but not found in index")
                continue
            i = cand[0]
            if target_arch:
                m = [c for c in cand if c.architecture == target_arch]
                if m:
                    i = m[0]
            stuff[d] = i

        total_dl = sum(p.size for p in stuff.values())
        total_inst = sum(p.installed_size for p in stuff.values())
        print_stage("Transaction Summary")
        for d in reversed(to_install):
            i = stuff.get(d)
            if not i:
                continue
            print(f"  Installing: {i.package} {i.architecture} "
                  f"{i.version} ({pretty_size(i.size).strip()})")
        print_sep()
        w = "Package" if len(to_install) == 1 else "Packages"
        print(f"  Install  {len(to_install)} {w}")
        print(f"  Total download size: {pretty_size(total_dl).strip()}")
        print(f"  Installed size:      {pretty_size(total_inst * 1024).strip()}")
        print()

        if not prompt_yes_no("Proceed with installation?"):
            return

        cache_dir = self.cache_path
        need_dl = []
        for d in reversed(to_install):
            i = stuff.get(d)
            if not i:
                continue
            if not (cache_dir / Path(i.filename).name).exists():
                need_dl.append(i)

        if need_dl:
            print_stage("Downloading Packages")
            n = len(need_dl)
            total_bytes = sum(p.size for p in need_dl)
            dlbar = ProgressBar(prefix="Total", total=total_bytes)
            dlbar.start_timer()
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                jobs = {}
                for idx, i in enumerate(need_dl, 1):
                    cnt = f"({idx}/{n}) "
                    jobs[pool.submit(
                        repo.download_deb, i.filename, cache_dir,
                        label=Path(i.filename).name, counter=cnt,
                    )] = i
                for fut in concurrent.futures.as_completed(jobs):
                    try:
                        p = fut.result()
                        if p and p.exists():
                            dlbar.update(p.stat().st_size)
                    except Exception as e:
                        print_warning(f"Download failed: {e}")
            dlbar.finish()
            print_sep()
            print()

        n_pkgs = len(to_install)
        installed_list = []
        for idx, d in enumerate(reversed(to_install), 1):
            i = stuff.get(d)
            if not i:
                continue
            deb_path = cache_dir / Path(i.filename).name

            if not deb_path.exists():
                print_error(f"Download failed for {d}")
                continue

            if download_only:
                print_info(f"Downloaded {d} (download-only mode)")
                continue

            is_explicit = explicit and (d == name.lower())
            label = f"{i.package}_{i.version}_{i.architecture}"
            print(f"  Installing: {label:<52s} {idx}/{n_pkgs}")
            self._install_deb(deb_path, i, explicit=is_explicit,
                               force=force, foreign_files=foreign_files)
            installed_list.append(label)

        if not download_only:
            self.db.write_pkg_list()
            print()
            print_stage("Installed")
            for l in installed_list:
                print(f"  {l}")
            print()
            print_success("Installation complete")

    def _install_deb(self, deb_path: Path, repo_info=None, explicit: bool = True,
                     force: bool = False, foreign_files: Dict[str, Set[str]] = None):
        if foreign_files is None:
            foreign_files = {}

        distro_family = get_distro_family()
        path_rewrite = make_path_rewrite(distro_family)

        if repo_info and (repo_info.sha256 or repo_info.md5sum):
            algo = "sha256" if repo_info.sha256 else "md5"
            expected = repo_info.sha256 or repo_info.md5sum
            if not verify_checksum(deb_path, expected, algo):
                print_error(f"Checksum mismatch for {deb_path.name}")
                deb_path.unlink(missing_ok=True)
                return

        pkg = DebPackage(deb_path)
        ctl = pkg.control
        name = ctl.package
        root = self._resolve_root()

        if path_rewrite:
            rewritten_files = [path_rewrite(f) for f in pkg.files]
            rewritten_conffiles = [path_rewrite(cf) for cf in pkg.conffiles]
        else:
            rewritten_files = list(pkg.files)
            rewritten_conffiles = list(pkg.conffiles)

        if not force and foreign_files:
            collisions: List[str] = []
            for pm_name, pm_files in foreign_files.items():
                for f in rewritten_files:
                    if ("/" + f.lstrip("/")) in pm_files:
                        collisions.append(f"[{pm_name}] {f}")
            if collisions:
                print_error(f"File conflicts with native package manager files: "
                             f"{', '.join(collisions[:5])}{'...' if len(collisions) > 5 else ''}")
                print_info("Use --force to override")
                return

        existing_pkg = self.db.get_package(name)
        old_files = []
        if existing_pkg:
            old_files = self.db.get_files(name)

        self._begin_transaction()

        if "preinst" in pkg.scripts:
            if not self._run_script(name, "preinst", pkg.scripts["preinst"],
                                    "install", fatal=True):
                self._rollback_transaction(root)
                print_error(f"Pre-installation script failed for {name}")
                return

        print_info(f"Unpacking {name} ({ctl.version})...")

        saved_conffiles = self._backup_conffiles(rewritten_conffiles, root)
        self._transaction_conffiles = saved_conffiles
        self._transaction_files = rewritten_files

        try:
            pkg.extract_data(root, path_rewrite=path_rewrite)
        except Exception as e:
            self._rollback_transaction(root)
            print_error(f"Extraction failed: {e}")
            return

        self._restore_conffiles(saved_conffiles)

        for f in rewritten_files:
            try:
                rewrite_file_contents(root, f, path_rewrite)
                rewrite_shebangs(root, f)
            except Exception as e:
                print_warning(f"could not rewrite {f}: {e}")

        self._run_ldconfig(rewritten_files, root)

        if "postinst" in pkg.scripts:
            self._run_script(name, "postinst", pkg.scripts["postinst"], "configure")

        installed = InstalledPackage(
            name=ctl.package,
            version=ctl.version,
            architecture=ctl.architecture,
            section=ctl.section,
            priority=ctl.priority,
            maintainer=ctl.maintainer,
            description=ctl.description.split("\n")[0],
            installed_size=ctl.installed_size,
            depends=ctl.depends,
            conflicts=ctl.conflicts,
            provides=ctl.provides,
            breaks=ctl.breaks,
            replaces=ctl.replaces,
        )
        pkg_id = self.db.add_package(installed, explicit=explicit)
        self.db.add_files(pkg_id, rewritten_files)
        if pkg.scripts:
            self.db.add_scripts(pkg_id, pkg.scripts)
        if pkg.triggers:
            self.db.add_triggers(pkg_id, pkg.triggers)

        if old_files:
            orphaned = set(old_files) - set(rewritten_files)
            for of in orphaned:
                full = root / of.lstrip("/")
                try:
                    if full.is_file() or full.is_symlink():
                        full.unlink(missing_ok=True)
                except Exception as e:
                    print_warning(f"could not remove orphaned {of}: {e}")

        self._transaction_files = []
        self._transaction_conffiles = []
        print_success(f"Installed {name} {ctl.version}")

    def _backup_conffiles(self, conffiles: List[str], root: Path) -> list:
        saved = []
        for cf in conffiles:
            full = root / cf.lstrip("/")
            if full.is_file():
                backup = Path(str(full) + ".tanuki-old")
                shutil.copy2(full, backup)
                saved.append((str(full), str(backup)))
            else:
                saved.append((str(full), None))
        return saved


    def _restore_conffiles(self, saved: list):
        for orig, backup in saved:
            if backup and os.path.exists(backup):
                new_content = Path(orig).read_bytes() if Path(orig).exists() else b""
                old_content = Path(backup).read_bytes()
                if new_content != old_content:
                    print_warning(f"Configuration file '{orig}' modified, "
                                  f"keeping new version (old saved as .tanuki-old)")
                Path(backup).unlink(missing_ok=True)

    def _run_ldconfig(self, files: List[str], root: Path):
        lib_paths = ("/usr/lib", "/lib", "/usr/local/lib", "/usr/lib64", "/lib64")
        has_so = any(
            f.startswith(p.lstrip("/")) and ".so" in f.rsplit("/", 1)[-1]
            for f in files
            for p in lib_paths
        )
        if has_so:
            try:
                ldconfig = shutil.which("ldconfig") or "/sbin/ldconfig"
                subprocess.run([ldconfig], timeout=30, capture_output=True)
            except Exception as e:
                print_warning(f"ldconfig failed: {e}")

    def _run_script(self, name: str, script_type: str, content: str, action: str,
                    fatal: bool = False) -> bool:
        print_info(f"Running {script_type} for {name}...")
        env = {**os.environ, "DPKG_MAINTSCRIPT_NAME": script_type,
               "DPKG_MAINTSCRIPT_PACKAGE": name}
        shim_dir = getattr(self, "_shim_dir", None)
        if shim_dir is None and self.db_path:
            from core.dpkg_shim import setup_shim
            arch = detect_architecture()
            try:
                shim_dir = setup_shim(self.db_path, arch)
                self._shim_dir = shim_dir
            except Exception as e:
                print_warning(f"dpkg shim setup failed: {e}")
        if shim_dir:
            bin_path = str(shim_dir / "bin")
            env["PATH"] = f"{bin_path}:{env.get('PATH', '')}"
        try:
            proc = subprocess.run(
                ["/bin/sh", "-e"],
                input=content,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if proc.returncode != 0:
                msg = f"{script_type} returned {proc.returncode}"
                if proc.stderr:
                    msg += f": {proc.stderr.strip()}"
                if fatal:
                    print_error(msg)
                    return False
                print_warning(msg)
            return True
        except subprocess.TimeoutExpired:
            msg = f"{script_type} timed out"
            if fatal:
                print_error(msg)
            else:
                print_warning(msg)
            return False
        except Exception as e:
            msg = f"Failed to run {script_type}: {e}"
            if fatal:
                print_error(msg)
            else:
                print_warning(msg)
            return False


    def remove(self, package_names: List[str]):
        self._require_root()
        if not package_names:
            print_error("Package name(s) required")
            return

        self._acquire_lock()
        try:
            self._check_system_locks()
            for name in package_names:
                self._remove_one_no_lock(name)
        finally:
            self._release_lock()


    def upgrade(self, force: bool = False):
        self._require_root()
        self._acquire_lock()
        try:
            self._check_system_locks()

            repo = self._get_repo()
            if not repo.index._packages:
                repo.update()

            installed = self.db.get_all_packages()
            upgradable = []
            upgrade_arch = self.config.get("arch", detect_architecture())
            for pkg in installed:
                candidates = repo.index.get(pkg.name)
                if not candidates:
                    continue
                if upgrade_arch:
                    arch_match = [c for c in candidates if c.architecture == upgrade_arch]
                    if arch_match:
                        candidates = arch_match
                repo_pkg = candidates[0]
                if _compare_versions(repo_pkg.version, pkg.version) > 0:
                    upgradable.append((pkg.name, pkg.version, repo_pkg.version))

            if not upgradable:
                print_info("All packages up to date")
                return

            print_info(f"Upgradable packages: {len(upgradable)}")
            data = [[n, ov, nv] for n, ov, nv in sorted(upgradable)]
            print_table(["Package", "Installed", "New"], data)

            if not prompt_yes_no("Proceed with upgrade?"):
                return

            host_pkgs = detect_host_packages(extra_lib_map=self._get_repo().lib_mapping or None)
            if host_pkgs:
                self.solver.load_installed(host_pkgs)
            self.solver.load_installed(
                {p.name: p.version for p in installed}
            )
            self.solver.load_installed_metadata(installed)
            if host_pkgs:
                self.solver.register_host_metadata(host_pkgs, self._get_repo().index)

            foreign_files = get_foreign_pm_files() if not force else {}

            to_install = []
            for pkg_name, old_ver, new_ver in upgradable:
                try:
                    deps = self.solver.resolve(pkg_name, repo.index)
                    for d in deps:
                        if d not in to_install:
                            to_install.append(d)
                except RuntimeError as e:
                    print_warning(f"Skipping {pkg_name}: {e}")

            if not to_install:
                print_info("Nothing to install")
                return

            cache = self.cache_path
            upgrade_arch = self.config.get("arch", detect_architecture())
            for dep_name in to_install:
                dep_candidates = repo.index.get(dep_name)
                if not dep_candidates:
                    print_error(f"Package '{dep_name}' resolved but not found")
                    continue
                dep_info = dep_candidates[0]
                if upgrade_arch:
                    arch_match = [c for c in dep_candidates if c.architecture == upgrade_arch]
                    if arch_match:
                        dep_info = arch_match[0]

                if dep_info.sha256 or dep_info.md5sum:
                    deb_check = cache / Path(dep_info.filename).name
                    if deb_check.exists():
                        algo = "sha256" if dep_info.sha256 else "md5"
                        expected = dep_info.sha256 or dep_info.md5sum
                        if not verify_checksum(deb_check, expected, algo):
                            print_warning(f"Checksum mismatch for cached {dep_name}, redownloading")
                            deb_check.unlink()

                deb_path = cache / Path(dep_info.filename).name
                if not deb_path.exists():
                    repo.download_deb(dep_info.filename, cache, label=dep_name)
                else:
                    print_info(f"Using cached {dep_name}")

                if not deb_path.exists():
                    print_error(f"Download failed for {dep_name}")
                    continue

                self._install_deb(deb_path, dep_info, force=force,
                                   foreign_files=foreign_files)

            self.db.write_pkg_list()
            print_success("Upgrade complete")
        finally:
            self._release_lock()


    def _cleanup_stale_cache(self, max_age_days=7):
        cache = self.cache_path
        if not cache.exists():
            return 0
        cutoff = datetime.now() - timedelta(days=max_age_days)
        count = 0
        for f in cache.iterdir():
            if f.is_file() and (f.name.endswith(".deb") or f.name.endswith(".part")):
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime < cutoff:
                        f.unlink()
                        count += 1
                except Exception as e:
                    print_warning(f"could not clean stale {f.name}: {e}")
        # also clean up any leftover partial downloads
        for f in cache.iterdir():
            if f.is_file() and f.name.endswith(".part"):
                try:
                    f.unlink()
                    count += 1
                except Exception as e:
                    print_warning(f"could not remove partial {f.name}: {e}")
        return count

    def clean(self):
        self._require_root()
        self._acquire_lock()
        try:
            cache = self.cache_path
            if cache.exists():
                count = 0
                for f in cache.iterdir():
                    if f.is_file():
                        f.unlink()
                        count += 1
                stale = self._cleanup_stale_cache(max_age_days=0)
                print_success(f"Cleaned {count + stale} cached packages")
            else:
                print_info("Cache directory is empty")
        finally:
            self._release_lock()

    def autoclean(self):
        self._require_root()
        self._acquire_lock()
        try:
            repo = self._get_repo()
            if not repo.index._packages:
                repo.update()

            cache = self.cache_path
            if not cache.exists():
                print_info("Cache directory is empty")
                return

            repo_filenames = set()
            for pkg in repo.index.all_packages():
                repo_filenames.add(Path(pkg.filename).name)

            count = 0
            for f in cache.iterdir():
                if f.is_file() and f.name.endswith(".deb"):
                    if f.name not in repo_filenames:
                        f.unlink()
                        count += 1

            print_success(f"Removed {count} stale packages from cache")
            extra = self._cleanup_stale_cache()
            if extra > 0:
                print_info(f"(also cleaned {extra} time-stale packages)")
        finally:
            self._release_lock()


    def autoremove(self):
        self._require_root()
        self._acquire_lock()
        try:
            auto_pkgs = self.db.get_auto_packages()
            if not auto_pkgs:
                print_info("No auto-installed packages to remove")
                return

            orphans = []
            for name in auto_pkgs:
                rdeps = self.db.get_reverse_depends(name)
                if not rdeps:
                    orphans.append(name)

            if not orphans:
                print_info("No orphaned packages found")
                return

            print_info(f"Orphaned auto-installed packages: {' '.join(orphans)}")
            if prompt_yes_no("Remove these packages?"):
                for name in orphans:
                    self._remove_one_no_lock(name)
        finally:
            self._release_lock()


    def update(self):
        print_info("Updating repository index...")
        repo = self._get_repo()
        if os.environ.get("TANUKI_SKIP_GPG"):
            print_info("GPG verification skipped (TANUKI_SKIP_GPG set)")
        elif not repo.verify_release():
            print_warning("GPG verification failed or unavailable")
            print_info("Set TANUKI_SKIP_GPG=1 to skip verification")
            if not prompt_yes_no("Continue without verification?"):
                return
        repo.update()
        try:
            repo.index.save(self.db_path / "repo-index.json")
        except Exception as e:
            print_warning(f"could not save repo index cache: {e}")
        stale = self._cleanup_stale_cache()
        if stale > 0:
            print_info(f"Cleaned {stale} stale cached packages")
        print_success(f"Index updated ({len(repo.index)} packages loaded)")


    def reinstall(self, package_name: str):
        if not package_name:
            print_error("Package name required")
            return
        self.remove([package_name])
        self.install([package_name])


    def purge(self, pattern: str):
        self._require_root()
        if not pattern:
            print_error("Pattern required")
            return

        self._acquire_lock()
        try:
            all_pkgs = self.db.get_all_packages()
            matches = [p for p in all_pkgs if pattern.lower() in p.name.lower()]
            if not matches:
                print_info(f"No packages matching '{pattern}' found")
                return
            names = [p.name for p in matches]
            print_info(f"Purging: {' '.join(names)}")
            if prompt_yes_no("Proceed?"):
                for name in names:
                    self._remove_one_no_lock(name)
        finally:
            self._release_lock()


    def _remove_one_no_lock(self, name: str):
        pkg = self.db.get_package(name)
        if not pkg:
            print_error(f"Package '{name}' is not installed")
            return

        scripts = self.db.get_scripts(name)
        root = self._resolve_root()

        if "prerm" in scripts:
            if not self._run_script(name, "prerm", scripts["prerm"],
                                    "remove", fatal=True):
                print_error(f"Pre-removal script failed for {name}, aborting")
                return
        files = self.db.get_files(name)
        for fpath in sorted(files, reverse=True):
            full = root / fpath.lstrip("/")
            if full.is_file() or full.is_symlink():
                full.unlink(missing_ok=True)

        if "postrm" in scripts:
            self._run_script(name, "postrm", scripts["postrm"], "remove")

        self.db.remove_package(name)
        self.db.write_pkg_list()
        print_success(f"Removed {name}")


    def list_packages(self, show_files: bool = False, pkg_name: Optional[str] = None):
        if show_files or pkg_name:
            if pkg_name:
                names = [pkg_name]
            else:
                names = [p.name for p in self.db.get_all_packages()]
            for n in names:
                files = self.db.get_files(n)
                if files:
                    print_info(f"{n}:")
                    for f in files:
                        print(f"  /{f.lstrip('/')}")
                else:
                    print_info(f"{n}: (no files tracked)")
            return
        packages = self.db.get_all_packages()
        if not packages:
            print_info("No packages installed")
            return
        data = [[p.name, p.version, p.architecture] for p in packages]
        print_table(["Package", "Version", "Arch"], data)


    def count(self):
        print(self.db.package_count())

    def verify(self, pkg_name: Optional[str] = None):
        import hashlib as _hl
        repo = self._get_repo()
        if not repo.index._packages:
            repo.update()
        if pkg_name:
            pkgs = [p for p in self.db.get_all_packages() if p.name == pkg_name]
        else:
            pkgs = self.db.get_all_packages()
        if not pkgs:
            print_info("No packages to verify")
            return
        issues = 0
        for pkg in pkgs:
            files = self.db.get_files(pkg.name)
            for f in files:
                full = self._resolve_root() / f.lstrip("/")
                if not full.exists():
                    print_warning(f"{pkg.name}: missing {f}")
                    issues += 1
                    continue
                candidates = repo.index.get(pkg.name)
                if not candidates:
                    continue
                rp = candidates[0]
                if rp.sha256:
                    h = _hl.sha256()
                    try:
                        h.update(full.read_bytes())
                    except Exception:
                        continue
                    if h.hexdigest() != rp.sha256:
                        print_warning(f"{pkg.name}: checksum mismatch {f}")
                        issues += 1
        if issues == 0:
            print_success("All files verified OK")
        else:
            print_warning(f"{issues} issue(s) found")


    def undo(self):
        snapshots_dir = self.db_path / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(snapshots_dir.glob("*.list"))
        if not existing:
            print_info("No snapshots to undo to")
            return
        latest = existing[-1]
        print_info(f"Found snapshot: {latest.stem}")
        if not prompt_yes_no("Restore this snapshot (will remove newer packages)?"):
            return
        current = {p.name for p in self.db.get_all_packages()}
        with open(latest) as f:
            lines = f.read().strip().split("\n")
        target = set()
        for l in lines:
            parts = l.split("\t")
            if parts:
                target.add(parts[0])
        to_remove = current - target
        to_install = target - current
        if to_remove:
            print_info(f"Removing: {' '.join(to_remove)}")
            for r in to_remove:
                self._remove_one_no_lock(r)
        if to_install:
            print_info(f"Installing: {' '.join(to_install)}")
            self.install(list(to_install))
        latest.unlink(missing_ok=True)
        print_success("Undo complete")


    def search(self, query: str):
        if not query:
            print_error("Search query required")
            return
        repo = self._get_repo()
        if not repo.index._packages:
            repo.update()
        results = repo.index.search(query)
        if not results:
            print_info(f"No packages found for '{query}'")
            return
        data = [[r.package, r.version, r.section, r.description[:60]]
                for r in results[:50]]
        print_table(["Package", "Version", "Section", "Description"], data)

    def info(self, package: str):
        if not package:
            print_error("Package name required")
            return

        pkg = self.db.get_package(package)
        if pkg:
            self._show_local_info(pkg)
            return

        repo = self._get_repo()
        if not repo.index._packages:
            repo.update()
        candidates = repo.index.get(package)
        if candidates:
            self._show_repo_info(candidates[0])
        else:
            print_error(f"Package '{package}' not found")

    def provides(self, virtual: str):
        if not virtual:
            print_error("Virtual package name required")
            return

        providers = self.db.get_packages_providing(virtual)

        repo = self._get_repo()
        if not repo.index._packages:
            repo.update()
        for pkgs in repo.index._packages.values():
            for p in pkgs:
                from core.dependency import _parse_provides
                for prov in _parse_provides(p.provides or ""):
                    if prov == virtual.lower():
                        if p.package not in providers:
                            providers.append(p.package)

        if not providers:
            print_info(f"No packages found providing '{virtual}'")
            return

        data = [[n, "(installed)" if self.db.is_installed(n) else "(available)"]
                for n in sorted(set(providers))]
        print_table(["Package", "Status"], data)


    def _show_local_info(self, pkg: InstalledPackage):
        print_info(f"Name:         {pkg.name}")
        print_info(f"Version:      {pkg.version}")
        print_info(f"Architecture: {pkg.architecture}")
        print_info(f"Section:      {pkg.section}")
        print_info(f"Priority:     {pkg.priority}")
        print_info(f"Status:       {pkg.status}")
        if pkg.depends:
            print_info(f"Depends:      {pkg.depends}")
        print_info(f"Description:  {pkg.description}")

    def _show_repo_info(self, pkg):
        print_info(f"Name:         {pkg.package}")
        print_info(f"Version:      {pkg.version}")
        print_info(f"Architecture: {pkg.architecture}")
        print_info(f"Section:      {pkg.section}")
        print_info(f"Priority:     {pkg.priority}")
        print_info(f"Maintainer:   {pkg.maintainer}")
        print_info(f"Size:         {pkg.size} bytes")
        if pkg.depends:
            print_info(f"Depends:      {pkg.depends}")
        if pkg.conflicts:
            print_info(f"Conflicts:    {pkg.conflicts}")
        print_info(f"Description:  {pkg.description}")

    def init(self):
        print_info("Initializing Tanuki...")
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.cache_path.mkdir(parents=True, exist_ok=True)
        (self.db_path / "info").mkdir(parents=True, exist_ok=True)
        self.db._init_db()

        config_path = self.db_path / "config.lua"
        if not config_path.exists():
            detected_arch = detect_architecture()
            config_path.write_text(
                f'mirror = "https://deb.debian.org/debian"\n'
                f'suite = "sid"\n'
                f'arch = "{detected_arch}"\n'
                f'components = {{ "main", "contrib", "non-free-firmware", "non-free" }}\n'
                f'architectures = {{ "{detected_arch}" }}\n'
            )
            print_info(f"Created default config at {config_path}")
            print_info(f"Detected architecture: {detected_arch}")

        print_success("Tanuki initialized")



    def snapshot(self, args: List[str]):
        snapshots_dir = self.db_path / "snapshots"

        if not args:
            if snapshots_dir.exists():
                snaps = sorted(snapshots_dir.glob("*.list"))
                if snaps:
                    data = [[s.stem, ""] for s in snaps]
                    print_table(["Snapshot", ""], data)
                else:
                    print_info("No snapshots found")
            else:
                print_info("No snapshots found")
            return

        action = args[0]
        existing = len(list(snapshots_dir.glob("*.list"))) if snapshots_dir.exists() else 0
        name = args[1] if len(args) > 1 else f"snapshot_{existing + 1}"
        name = Path(name).name

        if action == "create":
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            pkgs = self.db.get_all_packages()
            snapshot_file = snapshots_dir / f"{name}.list"
            snapshot_file.write_text(
                "\n".join(f"{p.name}\t{p.version}\t{p.architecture}" for p in pkgs)
            )
            print_success(f"Snapshot '{name}' created ({len(pkgs)} packages)")

        elif action == "restore":
            snapshot_file = snapshots_dir / f"{name}.list"
            if not snapshot_file.exists():
                print_error(f"Snapshot '{name}' not found")
                return
            print_info(f"Restoring from snapshot '{name}'...")
            with open(snapshot_file) as f:
                lines = f.read().strip().split("\n")
            packages = [l.split("\t")[0] for l in lines if l]
            if prompt_yes_no(f"Install {len(packages)} packages?"):
                self.install(packages)

        else:
            print_error(f"Unknown action: {action}")
            print_info("Use: tanuki snapshot [create|restore] [name]")
