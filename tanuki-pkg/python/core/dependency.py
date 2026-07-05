import re
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class DepRelation:
    name: str
    version_req: Optional[str] = None
    arch: Optional[str] = None
    alternatives: List["DepRelation"] = field(default_factory=list)
    is_or: bool = False


def parse_deps(raw: str) -> List[List[DepRelation]]:
    if not raw or raw.strip() == "":
        return []

    groups = []
    for group_str in _split_field(raw, ","):
        alternatives = []
        for part in _split_field(group_str, "|"):
            alternatives.append(_parse_one_dep(part.strip()))
        if alternatives:
            for a in alternatives[1:]:
                a.is_or = True
            groups.append(alternatives)
    return groups


def _split_field(text: str, sep: str) -> List[str]:
    parts = []
    current = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _parse_one_dep(raw: str) -> DepRelation:
    raw = raw.strip()
    arch = None
    if ":" in raw:
        name_part, _, rest = raw.partition(":")
        m = re.match(r"^(\S+?):([a-zA-Z0-9_-]+)(\s*\(.*)?$", raw)
        if m:
            raw = m.group(1) + (m.group(3) or "")
            arch = m.group(2)

    version_req = None
    m = re.match(r"^(\S+)\s*\(([^)]+)\)$", raw)
    if m:
        raw = m.group(1)
        version_req = m.group(2)
    else:
        raw = raw.strip()

    return DepRelation(name=raw, version_req=version_req, arch=arch)


def _split_version(ver: str) -> Tuple[str, str, str]:
    if ":" in ver:
        epoch, _, rest = ver.partition(":")
    else:
        epoch = "0"
        rest = ver
    if "-" in rest:
        upstream, _, revision = rest.partition("-")
    else:
        upstream = rest
        revision = ""
    return epoch, upstream, revision


def _compare_versions(a: str, b: str) -> int:
    if a == b:
        return 0
    ea, ua, ra = _split_version(a)
    eb, ub, rb = _split_version(b)

    try:
        iea, ieb = int(ea), int(eb)
    except ValueError:
        iea, ieb = 0, 0
    if iea != ieb:
        return -1 if iea < ieb else 1

    cmp = _compare_digit_strings(ua, ub)
    if cmp != 0:
        return cmp

    return _compare_digit_strings(ra, rb)


def _compare_digit_strings(sa: str, sb: str) -> int:
    i, j = 0, 0
    while i < len(sa) and j < len(sb):
        ca, cb = sa[i], sb[j]

        if ca == "~" and cb != "~":
            return -1
        if cb == "~" and ca != "~":
            return 1

        if ca.isdigit() and cb.isdigit():
            na, nb = "", ""
            while i < len(sa) and sa[i].isdigit():
                na += sa[i]; i += 1
            while j < len(sb) and sb[j].isdigit():
                nb += sb[j]; j += 1
            iva, ivb = int(na), int(nb)
            if iva != ivb:
                return -1 if iva < ivb else 1
        elif ca.isdigit():
            return 1
        elif cb.isdigit():
            return -1
        else:
            if ca != cb:
                return -1 if ca < cb else 1
            i += 1; j += 1

    if i < len(sa):
        return -1 if sa[i] == "~" else 1
    if j < len(sb):
        return 1 if sb[j] == "~" else -1
    return 0


def version_satisfies(actual: str, requirement: Optional[str]) -> bool:
    if requirement is None or requirement.strip() == "":
        return True

    req = requirement.strip()
    m = re.match(r"^(>=|<=|>>|<<|=)\s*(.*)$", req)
    if not m:
        return _compare_versions(actual, req) == 0

    op, target = m.group(1), m.group(2)
    cmp = _compare_versions(actual, target)

    if op == ">=":
        return cmp >= 0
    elif op == "<=":
        return cmp <= 0
    elif op == ">>":
        return cmp > 0
    elif op == "<<":
        return cmp < 0
    elif op == "=":
        return cmp == 0
    return False


class DependencySolver:

    def __init__(self, target_arch: Optional[str] = None):
        self.target_arch = target_arch
        self.provided: Dict[str, str] = {}
        self.conflict_map: Dict[str, List[str]] = {}
        self.installed_versions: Dict[str, str] = {}
        self.installed_conflicts: Dict[str, List[str]] = {}
        self.installed_provides: Dict[str, List[str]] = {}
        self.installed_breaks: Dict[str, List[str]] = {}
        self.installed_replaces: Dict[str, List[str]] = {}
        self.with_recommends = False

    def load_installed(self, installed_map: Dict[str, str]):
        self.installed_versions.update(installed_map)

    def load_installed_metadata(self, installed: List):
        for pkg in installed:
            name_lower = pkg.name.lower()
            self.installed_versions[name_lower] = pkg.version
            if pkg.conflicts:
                self.installed_conflicts[name_lower] = [
                    d.name.lower() for g in parse_deps(pkg.conflicts) for d in g
                ]
            if pkg.breaks:
                self.installed_breaks[name_lower] = [
                    d.name.lower() for g in parse_deps(pkg.breaks) for d in g
                ]
            if pkg.provides:
                for g in parse_deps(pkg.provides):
                    for d in g:
                        self.installed_provides.setdefault(d.name.lower(), []).append(name_lower)
            if pkg.replaces:
                self.installed_replaces[name_lower] = [
                    d.name.lower() for g in parse_deps(pkg.replaces) for d in g
                ]

    def check_conflicts(self, name: str, repo_index) -> List[str]:
        issues = []
        candidates = repo_index.get(name)
        if not candidates:
            return [f"package '{name}' not found"]
        pkg = candidates[0]
        name_lower = pkg.package.lower()

        for group in parse_deps(pkg.conflicts or ""):
            for dep in group:
                dep_lower = dep.name.lower()
                if dep_lower in self.installed_versions:
                    if version_satisfies(self.installed_versions[dep_lower], dep.version_req):
                        issues.append(f"conflicts with installed {dep.name}")

        for group in parse_deps(pkg.breaks or ""):
            for dep in group:
                dep_lower = dep.name.lower()
                if dep_lower in self.installed_versions:
                    if version_satisfies(self.installed_versions[dep_lower], dep.version_req):
                        issues.append(f"breaks installed {dep.name}")

        for other_name, conflicts in self.installed_conflicts.items():
            if name_lower in conflicts:
                issues.append(f"installed {other_name} conflicts with {name_lower}")

        for other_name, breaks in self.installed_breaks.items():
            if name_lower in breaks:
                issues.append(f"installed {other_name} breaks {name_lower}")

        return issues

    def resolve(self, wanted: str, repo_index) -> List[str]:
        to_visit = [wanted]
        resolved: List[str] = []
        seen: Set[str] = set()

        while to_visit:
            name = to_visit.pop(0).lower()
            if name in seen:
                continue
            seen.add(name)

            candidates = repo_index.get(name)
            if not candidates:
                raise RuntimeError(f"Package '{name}' not found in repository index")

            pkg = self._pick_candidate(candidates)
            if pkg.package.lower() not in resolved:
                resolved.append(pkg.package.lower())

            deps = parse_deps(pkg.depends or "")
            for group in deps:
                satisfied = False
                for alt in group:
                    if self._dep_met(alt, repo_index):
                        satisfied = True
                        if alt.name.lower() not in self.installed_versions:
                            self._add_to_visit(alt.name.lower(), repo_index, to_visit, seen, resolved)
                        break
                if not satisfied:
                    alt_names = [a.name for a in group]
                    raise RuntimeError(
                        f"Dependency not satisfied for '{pkg.package}': "
                        f"{' | '.join(alt_names)}"
                    )

            if self.with_recommends:
                recs = parse_deps(pkg.recommends or "")
                for group in recs:
                    for alt in group:
                        if not self._dep_met(alt, repo_index):
                            continue
                        if alt.name.lower() not in self.installed_versions and alt.name.lower() not in seen:
                            try:
                                self._add_to_visit(alt.name.lower(), repo_index, to_visit, seen, resolved)
                            except Exception:
                                pass
                            break

            pre_deps = parse_deps(pkg.pre_depends or "")
            for group in pre_deps:
                satisfied = False
                for alt in group:
                    if self._dep_met(alt, repo_index):
                        satisfied = True
                        if alt.name.lower() not in self.installed_versions:
                            self._add_to_visit(alt.name.lower(), repo_index, to_visit, seen, resolved)
                        break
                if not satisfied:
                    alt_names = [a.name for a in group]
                    raise RuntimeError(
                        f"Pre-dependency not satisfied for '{pkg.package}': "
                        f"{' | '.join(alt_names)}"
                    )

        return resolved

    def _dep_met(self, dep: DepRelation, repo_index) -> bool:
        if dep.name.lower() in self.installed_versions:
            return version_satisfies(
                self.installed_versions[dep.name.lower()],
                dep.version_req,
            )
        candidates = repo_index.get(dep.name)
        if candidates:
            best = self._pick_candidate(candidates)
            return version_satisfies(best.version, dep.version_req)
        if dep.name.lower() in self.installed_provides:
            for provider in self.installed_provides[dep.name.lower()]:
                if provider in self.installed_versions:
                    return version_satisfies(
                        self.installed_versions[provider], dep.version_req,
                    )
        for pkgs in repo_index._packages.values():
            for p in pkgs:
                if self.target_arch and p.architecture != self.target_arch:
                    continue
                provides = _parse_provides(p.provides)
                if dep.name.lower() in provides:
                    return version_satisfies(p.version, dep.version_req)
        return False

    def _pick_candidate(self, candidates):
        if self.target_arch:
            for c in candidates:
                if c.architecture == self.target_arch:
                    return c
        return candidates[0]

    def _find_providers(self, name: str, repo_index) -> List[str]:
        providers = []
        target = name.lower()
        for pkgs in repo_index._packages.values():
            for p in pkgs:
                if self.target_arch and p.architecture != self.target_arch:
                    continue
                for prov in _parse_provides(p.provides or ""):
                    if prov == target:
                        providers.append(p.package.lower())
                        break
        return providers

    def register_host_metadata(self, host_pkgs: Dict[str, str], repo_index):
        for name, version in host_pkgs.items():
            candidates = repo_index.get(name)
            if not candidates:
                continue
            pkg = self._pick_candidate(candidates)
            name_lower = pkg.package.lower()
            if pkg.conflicts:
                self.installed_conflicts[name_lower] = [
                    d.name.lower() for g in parse_deps(pkg.conflicts) for d in g
                ]
            if pkg.breaks:
                self.installed_breaks[name_lower] = [
                    d.name.lower() for g in parse_deps(pkg.breaks) for d in g
                ]
            if pkg.provides:
                for g in parse_deps(pkg.provides):
                    for d in g:
                        self.installed_provides.setdefault(d.name.lower(), []).append(name_lower)
            if pkg.replaces:
                self.installed_replaces[name_lower] = [
                    d.name.lower() for g in parse_deps(pkg.replaces) for d in g
                ]

    def _add_to_visit(self, name: str, repo_index, to_visit: list, seen: set, resolved: list):
        lower = name.lower()
        if lower in seen or lower in resolved:
            return
        if repo_index.get(lower):
            to_visit.append(lower)
            return
        for prov in self._find_providers(lower, repo_index):
            if prov not in seen and prov not in resolved and prov not in self.installed_versions:
                to_visit.append(prov)


def _parse_provides(raw: str) -> List[str]:
    if not raw:
        return []
    provides = []
    for part in _split_field(raw, ","):
        m = re.match(r"^(\S+)", part.strip())
        if m:
            provides.append(m.group(1).lower())
    return provides
