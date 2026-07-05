import sqlite3
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict



@dataclass
class InstalledPackage:
    name: str
    version: str
    architecture: str
    section: str = ""
    priority: str = ""
    maintainer: str = ""
    description: str = ""
    installed_size: int = 0
    status: str = "installed"
    install_date: str = ""
    depends: str = ""
    conflicts: str = ""
    provides: str = ""
    breaks: str = ""
    replaces: str = ""

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    architecture TEXT NOT NULL,
    section TEXT DEFAULT '',
    priority TEXT DEFAULT 'optional',
    maintainer TEXT DEFAULT '',
    description TEXT DEFAULT '',
    installed_size INTEGER DEFAULT 0,
    status TEXT DEFAULT 'installed',
    install_date TEXT DEFAULT (datetime('now')),
    depends TEXT DEFAULT '',
    conflicts TEXT DEFAULT '',
    provides TEXT DEFAULT '',
    breaks TEXT DEFAULT '',
    replaces TEXT DEFAULT '',
    explicit INTEGER DEFAULT 1,
    UNIQUE(name, architecture)
);

CREATE TABLE IF NOT EXISTS package_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    hash TEXT DEFAULT '',
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE,
    UNIQUE(package_id, path)
);

CREATE TABLE IF NOT EXISTS package_scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER NOT NULL,
    script_type TEXT NOT NULL,
    content TEXT DEFAULT '',
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alternatives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    package_id INTEGER,
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS config_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    hash TEXT DEFAULT '',
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS diversions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package TEXT NOT NULL,
    original_path TEXT NOT NULL,
    diverted_path TEXT NOT NULL,
    rename INTEGER DEFAULT 1,
    UNIQUE(original_path)
);

CREATE TABLE IF NOT EXISTS package_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER NOT NULL,
    trigger_name TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE
);
"""


class PackageDatabase:
    def __init__(self, root: Path):
        self.root = Path(root)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            fallback = Path.home() / ".local/share/tanuki"
            try:
                fallback.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                fallback = Path("/tmp/tanuki-db")
                fallback.mkdir(parents=True, exist_ok=True)
            self.root = fallback
        self.db_path = self.root / "tanuki.db"
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(str(self.db_path))
        except PermissionError:
            fallback = Path("/tmp/tanuki-db")
            fallback.mkdir(parents=True, exist_ok=True)
            self.root = fallback
            self.db_path = fallback / "tanuki.db"
            conn = sqlite3.connect(str(self.db_path))
        conn.executescript(SCHEMA)
        try:
            conn.execute("ALTER TABLE packages ADD COLUMN explicit INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE packages ADD COLUMN breaks TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE packages ADD COLUMN replaces TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def add_package(self, pkg: InstalledPackage, explicit: bool = True) -> int:
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT id FROM packages WHERE name=? AND architecture=?",
                (pkg.name, pkg.architecture),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE packages SET version=?, section=?, priority=?, maintainer=?,
                       description=?, installed_size=?, status=?, install_date=datetime('now'),
                       depends=?, conflicts=?, provides=?, breaks=?, replaces=?, explicit=?
                       WHERE id=?""",
                    (
                        pkg.version, pkg.section, pkg.priority, pkg.maintainer,
                        pkg.description, pkg.installed_size, pkg.status,
                        pkg.depends, pkg.conflicts, pkg.provides, pkg.breaks,
                        pkg.replaces, int(explicit), existing["id"],
                    ),
                )
                conn.commit()
                return existing["id"]
            c = conn.execute(
                """INSERT INTO packages
                   (name, version, architecture, section, priority, maintainer,
                    description, installed_size, status, install_date, depends,
                    conflicts, provides, breaks, replaces, explicit)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?)""",
                (
                    pkg.name, pkg.version, pkg.architecture, pkg.section,
                    pkg.priority, pkg.maintainer, pkg.description,
                    pkg.installed_size, pkg.status, pkg.depends,
                    pkg.conflicts, pkg.provides, pkg.breaks, pkg.replaces,
                    int(explicit),
                ),
            )
            conn.commit()
            return c.lastrowid
        finally:
            conn.close()

    def remove_package(self, name: str, architecture: Optional[str] = None):
        conn = self._connect()
        try:
            if architecture:
                conn.execute(
                    "DELETE FROM packages WHERE name=? AND architecture=?",
                    (name, architecture),
                )
            else:
                conn.execute("DELETE FROM packages WHERE name=?", (name,))
            conn.commit()
        finally:
            conn.close()

    def get_package(self, name: str) -> Optional[InstalledPackage]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM packages WHERE name=? ORDER BY install_date DESC LIMIT 1",
                (name,),
            ).fetchone()
            if row is None:
                return None
            return InstalledPackage(
                name=row["name"],
                version=row["version"],
                architecture=row["architecture"],
                section=row["section"],
                priority=row["priority"],
                maintainer=row["maintainer"],
                description=row["description"],
                installed_size=row["installed_size"],
                status=row["status"],
                install_date=row["install_date"],
                depends=row["depends"],
                conflicts=row["conflicts"],
                provides=row["provides"],
                breaks=row["breaks"] if "breaks" in row.keys() else "",
                replaces=row["replaces"] if "replaces" in row.keys() else "",
            )
        finally:
            conn.close()

    def get_all_packages(self) -> List[InstalledPackage]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM packages ORDER BY name"
            ).fetchall()
            return [
                InstalledPackage(
                    name=r["name"], version=r["version"],
                    architecture=r["architecture"], section=r["section"],
                    priority=r["priority"], maintainer=r["maintainer"],
                    description=r["description"],
                    installed_size=r["installed_size"],
                    status=r["status"], install_date=r["install_date"],
                    depends=r["depends"], conflicts=r["conflicts"],
                    provides=r["provides"],
                    breaks=r["breaks"] if "breaks" in r.keys() else "",
                    replaces=r["replaces"] if "replaces" in r.keys() else "",
                )
                for r in rows
            ]
        finally:
            conn.close()

    def set_package_status(self, name: str, status: str):
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE packages SET status=? WHERE name=?",
                (status, name),
            )
            conn.commit()
        finally:
            conn.close()

    def add_files(self, package_id: int, file_paths: List[str]):
        conn = self._connect()
        try:
            conn.executemany(
                "INSERT OR IGNORE INTO package_files (package_id, path) VALUES (?, ?)",
                [(package_id, p) for p in file_paths],
            )
            conn.commit()
        finally:
            conn.close()

    def get_files(self, name: str) -> List[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT pf.path FROM package_files pf
                   JOIN packages p ON pf.package_id = p.id
                   WHERE p.name = ?""",
                (name,),
            ).fetchall()
            return [r["path"] for r in rows]
        finally:
            conn.close()

    def remove_files(self, name: str):
        conn = self._connect()
        try:
            conn.execute(
                """DELETE FROM package_files WHERE package_id IN
                   (SELECT id FROM packages WHERE name=?)""",
                (name,),
            )
            conn.commit()
        finally:
            conn.close()

    def add_scripts(self, package_id: int, scripts: Dict[str, str]):
        conn = self._connect()
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO package_scripts (package_id, script_type, content) VALUES (?, ?, ?)",
                [(package_id, stype, content) for stype, content in scripts.items()],
            )
            conn.commit()
        finally:
            conn.close()

    def get_scripts(self, name: str) -> Dict[str, str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT ps.script_type, ps.content FROM package_scripts ps
                   JOIN packages p ON ps.package_id = p.id
                   WHERE p.name = ?""",
                (name,),
            ).fetchall()
            return {r["script_type"]: r["content"] for r in rows}
        finally:
            conn.close()

    def file_owned_by(self, filepath: str) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT p.name FROM packages p
                   JOIN package_files pf ON pf.package_id = p.id
                   WHERE pf.path = ?""",
                (filepath,),
            ).fetchone()
            return row["name"] if row else None
        finally:
            conn.close()

    def is_installed(self, name: str) -> bool:
        return self.get_package(name) is not None

    def get_auto_packages(self) -> List[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM packages WHERE explicit=0"
            ).fetchall()
            return [r["name"] for r in rows]
        finally:
            conn.close()

    def get_reverse_depends(self, name: str) -> List[str]:
        target = name.lower()
        result = []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name, depends FROM packages WHERE depends != ''"
            ).fetchall()
            for r in rows:
                deps_raw = r["depends"]
                for group in deps_raw.split(","):
                    for alt in group.split("|"):
                        alt_name = alt.strip().split("(")[0].strip().rstrip(":").lower()
                        if alt_name == target:
                            result.append(r["name"])
                            break
            return result
        finally:
            conn.close()

    def get_packages_providing(self, virtual: str) -> List[str]:
        target = virtual.lower()
        result = []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name, provides FROM packages WHERE provides != ''"
            ).fetchall()
            for r in rows:
                for prov in r["provides"].split(","):
                    prov_name = prov.strip().split("(")[0].strip().lower()
                    if prov_name == target:
                        result.append(r["name"])
                        break
            return result
        finally:
            conn.close()

    def set_explicit(self, name: str, explicit: bool):
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE packages SET explicit=? WHERE name=?",
                (int(explicit), name),
            )
            conn.commit()
        finally:
            conn.close()

    def add_diversion(self, pkg: str, orig: str, div: str, rename: int = 1):
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO diversions (package, original_path, diverted_path, rename) VALUES (?, ?, ?, ?)",
                (pkg, orig, div, rename),
            )
            conn.commit()
        finally:
            conn.close()

    def remove_diversion(self, orig: str):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM diversions WHERE original_path=?", (orig,))
            conn.commit()
        finally:
            conn.close()

    def get_diversions(self) -> List[Dict]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM diversions").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_truename(self, path: str) -> str:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT diverted_path FROM diversions WHERE original_path=?", (path,)
            ).fetchone()
            return row["diverted_path"] if row else path
        finally:
            conn.close()

    def get_original(self, diverted: str) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT original_path FROM diversions WHERE diverted_path=?", (diverted,)
            ).fetchone()
            return row["original_path"] if row else None
        finally:
            conn.close()

    def add_triggers(self, pkg_id: int, triggers: List[str]):
        if not triggers:
            return
        conn = self._connect()
        try:
            for line in triggers:
                parts = line.strip().split(None, 1)
                if len(parts) == 2:
                    ttype, tname = parts
                    conn.execute(
                        "INSERT OR IGNORE INTO package_triggers (package_id, trigger_name, trigger_type) VALUES (?, ?, ?)",
                        (pkg_id, tname, ttype),
                    )
            conn.commit()
        finally:
            conn.close()

    def get_interest_triggers(self, trigger_name: str) -> List[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT p.name FROM packages p
                   JOIN package_triggers pt ON pt.package_id = p.id
                   WHERE pt.trigger_name=? AND pt.trigger_type IN ('interest', 'interest-await')""",
                (trigger_name,),
            ).fetchall()
            return [r["name"] for r in rows]
        finally:
            conn.close()

    def get_package_files_for_verification(self) -> List[Dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT p.name as pkg, p.version, pf.path FROM packages p
                   JOIN package_files pf ON pf.package_id = p.id
                   ORDER BY p.name"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
