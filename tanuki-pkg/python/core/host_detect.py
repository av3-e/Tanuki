import os
import shutil
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

HOST_VERSION_SENTINEL = "999"

_DISTRO_CACHE: Optional[Dict[str, str]] = None


def detect_distro() -> Dict[str, str]:
    global _DISTRO_CACHE
    if _DISTRO_CACHE is not None:
        return _DISTRO_CACHE

    info: Dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        key, _, val = line.partition("=")
                        val = val.strip("\"'")
                        info[key] = val
        except (FileNotFoundError, PermissionError):
            continue
        if info:
            break

    if not info:
        if os.path.exists("/etc/arch-release"):
            info["ID"] = "arch"
        elif os.path.exists("/etc/fedora-release"):
            info["ID"] = "fedora"
        elif os.path.exists("/etc/redhat-release"):
            info["ID"] = "rhel"
        elif os.path.exists("/etc/SuSE-release"):
            info["ID"] = "suse"
        elif os.path.exists("/etc/alpine-release"):
            info["ID"] = "alpine"
        elif os.path.exists("/etc/gentoo-release"):
            info["ID"] = "gentoo"
        elif os.path.exists("/etc/slackware-version"):
            info["ID"] = "slackware"
        elif os.path.exists("/etc/void-release"):
            info["ID"] = "void"
        elif os.path.exists("/etc/exherbo-release"):
            info["ID"] = "exherbo"

    _DISTRO_CACHE = info
    return info


def get_distro_family(info: Optional[Dict[str, str]] = None) -> str:
    if info is None:
        info = detect_distro()
    distro_id = info.get("ID", "").lower()
    distro_id_like = info.get("ID_LIKE", "").lower()

    if distro_id in ("arch", "archlinux", "manjaro", "endeavouros", "garuda", "arcolinux"):
        return "arch"
    if distro_id in ("fedora",) or "fedora" in distro_id_like:
        return "fedora"
    if distro_id in ("rhel", "centos", "almalinux", "rocky", "ol", "nobara") or "rhel" in distro_id_like:
        return "rhel"
    if distro_id in ("opensuse", "opensuse-tumbleweed", "opensuse-leap", "suse") or "suse" in distro_id_like:
        return "suse"
    if distro_id in ("alpine",) or "alpine" in distro_id_like:
        return "alpine"
    if distro_id in ("debian", "ubuntu", "linuxmint", "pop", "zorin", "kali", "elementary", "mx", "parrot", "deepin"):
        return "debian"
    if distro_id in ("void",) or "void" in distro_id_like:
        return "void"
    if distro_id in ("gentoo", "calculate", "funtoo") or "gentoo" in distro_id_like:
        return "gentoo"
    if distro_id in ("nixos",):
        return "nixos"
    if distro_id in ("slackware", "salix"):
        return "slackware"
    if distro_id in ("solus",):
        return "solus"
    if distro_id in ("exherbo",):
        return "exherbo"
    if distro_id in ("guix", "guixsd"):
        return "guix"

    return "unknown"


def detect_architecture() -> str:
    arch_env = os.environ.get("TANUKI_ARCH", "").lower()
    if arch_env:
        return arch_env
    try:
        uname = os.uname().machine.lower()
    except AttributeError:
        uname = ""

    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "armhf",
        "armv8l": "armhf",
        "arm": "armhf",
        "i386": "i386",
        "i686": "i386",
        "riscv64": "riscv64",
        "s390x": "s390x",
        "ppc64le": "ppc64el",
        "ppc64": "ppc64",
    }
    return mapping.get(uname, "amd64")


ARCH_TRIPLES: Dict[str, str] = {
    "amd64": "x86_64-linux-gnu",
    "arm64": "aarch64-linux-gnu",
    "armhf": "arm-linux-gnueabihf",
    "armel": "arm-linux-gnueabi",
    "i386": "i386-linux-gnu",
    "mips": "mips-linux-gnu",
    "mips64el": "mips64el-linux-gnuabi64",
    "mipsel": "mipsel-linux-gnu",
    "powerpc": "powerpc-linux-gnu",
    "ppc64": "powerpc-linux-gnu",
    "ppc64el": "powerpc64le-linux-gnu",
    "riscv64": "riscv64-linux-gnu",
    "s390x": "s390x-linux-gnu",
}


def make_path_rewrite(distro_family: str) -> Optional[Callable[[str], str]]:
    if distro_family == "debian":
        return None
    triples = set(ARCH_TRIPLES.values())
    prefixes: List[str] = []
    for triple in triples:
        for base in ("usr/lib", "lib", "usr/include"):
            prefixes.append(f"{base}/{triple}")
    def _rewrite(path: str) -> str:
        p = path.lstrip("./")
        for prefix in prefixes:
            if p == prefix or p.startswith(prefix + "/"):
                rest = p[len(prefix):]
                return prefix.rsplit("/", 1)[0] + rest
        return p
    return _rewrite


def _parse_ld_so_conf(path: str = "/etc/ld.so.conf") -> List[str]:
    paths: List[str] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("include "):
                    pattern = line[len("include "):].strip()
                    base_dir = os.path.dirname(path)
                    for glob_path in _expand_glob(os.path.join(base_dir, pattern) if not pattern.startswith("/") else pattern):
                        paths.extend(_parse_ld_so_conf(glob_path))
                else:
                    paths.append(line)
    except (FileNotFoundError, PermissionError):
        pass
    return paths


def _expand_glob(pattern: str) -> List[str]:
    import glob as glob_mod
    try:
        return glob_mod.glob(pattern)
    except Exception:
        return []


def _get_ldconfig_paths() -> Dict[str, str]:
    soname_to_path: Dict[str, str] = {}
    try:
        proc = subprocess.run(
            ["ldconfig", "-p"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                m = re.match(r"^\s*(\S+)\s+\([^)]*\)\s*=>\s*(\S+)", line)
                if m:
                    soname_to_path[m.group(1)] = m.group(2)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return soname_to_path


def get_library_search_paths() -> List[str]:
    paths: List[str] = []
    seen: Set[str] = set()

    ld_conf = _parse_ld_so_conf()
    for p in ld_conf:
        p = p.rstrip("/")
        if os.path.isdir(p) and p not in seen:
            paths.append(p)
            seen.add(p)

    if not paths:
        base = ["/usr/lib", "/lib"]
        try:
            arch = os.uname().machine.lower()
            if arch == "x86_64":
                base.extend(["/usr/lib64", "/lib64", "/usr/lib/x86_64-linux-gnu"])
            elif "arm" in arch:
                base.append(f"/usr/lib/{arch}-linux-gnueabihf")
            elif arch.startswith("aarch64"):
                base.append("/usr/lib/aarch64-linux-gnu")
            elif arch.startswith("riscv"):
                base.append("/usr/lib/riscv64-linux-gnu")
        except AttributeError:
            pass
        for p in base:
            if os.path.isdir(p) and p not in seen:
                paths.append(p)
                seen.add(p)

    ld_library_path = os.environ.get("LD_LIBRARY_PATH", "")
    for p in ld_library_path.split(":"):
        p = p.strip().rstrip("/")
        if p and os.path.isdir(p) and p not in seen:
            paths.insert(0, p)
            seen.add(p)

    return paths


LIB_MAP: Dict[str, str] = {
    "libc.so.6": "libc6",
    "libm.so.6": "libc6",
    "libdl.so.2": "libc6",
    "libpthread.so.0": "libc6",
    "librt.so.1": "libc6",
    "libresolv.so.2": "libc6",
    "libutil.so.1": "libc6",
    "libnss_files.so.2": "libc6",
    "libnss_dns.so.2": "libc6",
    "libnss_compat.so.2": "libc6",
    "libnss_nis.so.2": "libc6",
    "libnss_hesiod.so.2": "libc6",
    "libnss_ldap.so.2": "libc6",
    "libnss_mdns4.so.2": "libc6",
    "libnss_mdns6.so.2": "libc6",
    "libnss_mymachines.so.2": "libc6",
    "libnss_resolve.so.2": "libc6",
    "libnss_systemd.so.2": "libc6",
    "libnss_myhostname.so.2": "libc6",
    "libanl.so.1": "libc6",
    "libcidn.so.1": "libc6",
    "libBrokenLocale.so.1": "libc6",
    "ld-linux-x86-64.so.2": "libc6",
    "ld-linux.so.2": "libc6",
    "ld-linux-aarch64.so.1": "libc6",
    "ld-linux-armhf.so.3": "libc6",
    "ld-linux-riscv64.so.1": "libc6",
    "libpthread-stubs.so.0": "libpthread-stubs0-0",
    "libncurses.so.6": "libncurses6",
    "libncursesw.so.6": "libncurses6",
    "libtinfo.so.6": "libncurses6",
    "libform.so.6": "libncurses6",
    "libpanel.so.6": "libncurses6",
    "libmenu.so.6": "libncurses6",
    "libncurses++.so.6": "libncurses6",
    "libncurses.so.5": "libncurses5",
    "libncursesw.so.5": "libncurses5",
    "libtinfo.so.5": "libncurses5",
    "libform.so.5": "libncurses5",
    "libpanel.so.5": "libncurses5",
    "libmenu.so.5": "libncurses5",
    "libz.so.1": "zlib1g",
    "libzstd.so.1": "libzstd1",
    "libbz2.so.1.0": "libbz2-1.0",
    "libbz2.so.1": "libbz2-1.0",
    "liblzma.so.5": "liblzma5",
    "liblz4.so.1": "liblz4-1",
    "libsnappy.so.1": "libsnappy1v5",
    "libssl.so.3": "libssl3",
    "libcrypto.so.3": "libssl3",
    "libssl.so.1.1": "libssl1.1",
    "libcrypto.so.1.1": "libssl1.1",
    "libsqlite3.so.0": "libsqlite3-0",
    "libffi.so.8": "libffi8",
    "libffi.so.7": "libffi7",
    "libffi.so.6": "libffi6",
    "libexpat.so.1": "libexpat1",
    "libreadline.so.8": "libreadline8",
    "libhistory.so.8": "libreadline8",
    "libreadline.so.7": "libreadline7",
    "libhistory.so.7": "libreadline7",
    "libpcre2-8.so.0": "libpcre2-8-0",
    "libpcre2-16.so.0": "libpcre2-16-0",
    "libpcre2-32.so.0": "libpcre2-32-0",
    "libpcre.so.1": "libpcre3",
    "libpcre.so.3": "libpcre3",
    "libpcrecpp.so.0": "libpcre3",
    "libpcreposix.so.0": "libpcre3",
    "libpcre16.so.0": "libpcre3",
    "libpcre32.so.0": "libpcre3",
    "libxml2.so.2": "libxml2",
    "libxslt.so.1": "libxslt1.1",
    "libexslt.so.0": "libxslt1.1",
    "libcurl.so.4": "libcurl4",
    "libsystemd.so.0": "libsystemd0",
    "libudev.so.1": "libudev1",
    "libnss_systemd.so.2": "libsystemd0",
    "libcap.so.2": "libcap2",
    "libcap-ng.so.0": "libcap-ng0",
    "libgcc_s.so.1": "libgcc-s1",
    "libstdc++.so.6": "libstdc++6",
    "libatomic.so.1": "libatomic1",
    "libgfortran.so.5": "libgfortran5",
    "libgomp.so.1": "libgomp1",
    "libitm.so.1": "libitm1",
    "libquadmath.so.0": "libquadmath0",
    "libubsan.so.1": "libubsan1",
    "libasan.so.6": "libasan6",
    "libasan.so.8": "libasan8",
    "liblsan.so.0": "liblsan0",
    "libtsan.so.2": "libtsan2",
    "libssp.so.0": "libssp0",
    "libgmp.so.10": "libgmp10",
    "libgmpxx.so.4": "libgmp10",
    "libmpfr.so.6": "libmpfr6",
    "libmpc.so.3": "libmpc3",
    "libgnutls.so.30": "libgnutls30",
    "libnettle.so.8": "libnettle8",
    "libhogweed.so.6": "libhogweed6",
    "libp11-kit.so.0": "libp11-kit0",
    "libtasn1.so.6": "libtasn1-6",
    "libidn2.so.0": "libidn2-0",
    "libidn.so.12": "libidn12",
    "libidn.so.11": "libidn11",
    "libunistring.so.5": "libunistring5",
    "libgdbm.so.6": "libgdbm6",
    "libgdbm_compat.so.4": "libgdbm6",
    "libcrypt.so.1": "libcrypt1",
    "libcrypt.so.2": "libcrypt2",
    "libselinux.so.1": "libselinux1",
    "libaudit.so.1": "libaudit1",
    "libauparse.so.0": "libaudit1",
    "libseccomp.so.2": "libseccomp2",
    "libelf.so.1": "libelf1",
    "libdw.so.1": "libdw1",
    "libarchive.so.13": "libarchive13",
    "libicudata.so.76": "libicu76",
    "libicudata.so.75": "libicu75",
    "libicudata.so.74": "libicu74",
    "libicudata.so.73": "libicu73",
    "libicudata.so.72": "libicu72",
    "libicudata.so.71": "libicu71",
    "libicudata.so.70": "libicu70",
    "libicudata.so.69": "libicu69",
    "libicudata.so.68": "libicu68",
    "libicudata.so.67": "libicu67",
    "libicui18n.so.76": "libicu76",
    "libicui18n.so.75": "libicu75",
    "libicui18n.so.74": "libicu74",
    "libicuuc.so.76": "libicu76",
    "libicuuc.so.75": "libicu75",
    "libicuuc.so.74": "libicu74",
    "libedit.so.2": "libedit2",
    "libsasl2.so.2": "libsasl2-2",
    "libsasl2.so.3": "libsasl2-2",
    "libgcrypt.so.20": "libgcrypt20",
    "libgpg-error.so.0": "libgpg-error0",
    "libksba.so.8": "libksba8",
    "libusb-1.0.so.0": "libusb-1.0-0",
    "libusb-0.1.so.4": "libusb-0.1-4",
    "libjpeg.so.62": "libjpeg62-turbo",
    "libjpeg.so.8": "libjpeg8",
    "libturbojpeg.so.0": "libturbojpeg0",
    "libpng16.so.16": "libpng16-16",
    "libpng12.so.0": "libpng12-0",
    "libfreetype.so.6": "libfreetype6",
    "libfontconfig.so.1": "libfontconfig1",
    "libharfbuzz.so.0": "libharfbuzz0b",
    "libharfbuzz-icu.so.0": "libharfbuzz0b",
    "libharfbuzz-subset.so.0": "libharfbuzz0b",
    "libcairo.so.2": "libcairo2",
    "libcairo-gobject.so.2": "libcairo2",
    "libcairo-script-interpreter.so.2": "libcairo2",
    "libpixman-1.so.0": "libpixman-1-0",
    "libfribidi.so.0": "libfribidi0",
    "libbrotlidec.so.1": "libbrotli1",
    "libbrotlienc.so.1": "libbrotli1",
    "libbrotlicommon.so.1": "libbrotli1",
    "libwebp.so.7": "libwebp7",
    "libwebp.so.6": "libwebp6",
    "libwebpmux.so.3": "libwebpmux3",
    "libwebpdemux.so.2": "libwebpdemux2",
    "libtiff.so.6": "libtiff6",
    "libtiff.so.5": "libtiff5",
    "libtiffxx.so.6": "libtiff6",
    "liblcms2.so.2": "liblcms2-2",
    "libglib-2.0.so.0": "libglib2.0-0",
    "libgobject-2.0.so.0": "libglib2.0-0",
    "libgio-2.0.so.0": "libglib2.0-0",
    "libgmodule-2.0.so.0": "libglib2.0-0",
    "libgthread-2.0.so.0": "libglib2.0-0",
    "libglib-2.0-2.0.so.0": "libglib2.0-0",
    "libdbus-1.so.3": "libdbus-1-3",
    "libx11.so.6": "libx11-6",
    "libxcb.so.1": "libxcb1",
    "libxcb-render.so.0": "libxcb-render0",
    "libxcb-shm.so.0": "libxcb-shm0",
    "libxcb-xfixes.so.0": "libxcb-xfixes0",
    "libxcb-shape.so.0": "libxcb-shape0",
    "libxcb-randr.so.0": "libxcb-randr0",
    "libxcb-keysyms.so.1": "libxcb-keysyms1",
    "libxcb-image.so.0": "libxcb-image0",
    "libxcb-icccm.so.4": "libxcb-icccm4",
    "libXau.so.6": "libxau6",
    "libXdmcp.so.6": "libxdmcp6",
    "libXext.so.6": "libxext6",
    "libXrender.so.1": "libxrender1",
    "libXrandr.so.2": "libxrandr2",
    "libXfixes.so.3": "libxfixes3",
    "libXi.so.6": "libxi6",
    "libXt.so.6": "libxt6",
    "libXtst.so.6": "libxtst6",
    "libXcursor.so.1": "libxcursor1",
    "libXinerama.so.1": "libxinerama1",
    "libXdamage.so.1": "libxdamage1",
    "libXcomposite.so.1": "libxcomposite1",
    "libX11-xcb.so.1": "libx11-6",
    "libXxf86vm.so.1": "libxxf86vm1",
    "libXv.so.1": "libxv1",
    "libXpresent.so.1": "libxpresent1",
    "libXpm.so.4": "libxpm4",
    "libXmu.so.6": "libxmu6",
    "libXss.so.1": "libxss1",
    "libxkbcommon.so.0": "libxkbcommon0",
    "libxkbcommon-x11.so.0": "libxkbcommon-x11-0",
    "libwayland-client.so.0": "libwayland-client0",
    "libwayland-server.so.0": "libwayland-server0",
    "libwayland-cursor.so.0": "libwayland-cursor0",
    "libwayland-egl.so.1": "libwayland-egl1",
    "libEGL.so.1": "libegl1",
    "libGL.so.1": "libgl1",
    "libGLESv2.so.2": "libgles2",
    "libglapi.so.0": "libglapi-mesa",
    "libdrm.so.2": "libdrm2",
    "libdrm_amdgpu.so.1": "libdrm-amdgpu1",
    "libdrm_radeon.so.1": "libdrm-radeon1",
    "libdrm_nouveau.so.2": "libdrm-nouveau2",
    "libdrm_intel.so.1": "libdrm-intel1",
    "libpulse.so.0": "libpulse0",
    "libpulse-simple.so.0": "libpulse0",
    "libpulse-mainloop-glib.so.0": "libpulse0",
    "libasound.so.2": "libasound2",
    "libasound_module*.so": "libasound2",
    "libpipewire-0.3.so.0": "libpipewire-0.3-0",
    "libspa-0.2.so.0": "libspa-0.2-0",
    "libSDL2-2.0.so.0": "libsdl2-2.0-0",
    "libSDL2_image-2.0.so.0": "libsdl2-image-2.0-0",
    "libSDL2_ttf-2.0.so.0": "libsdl2-ttf-2.0-0",
    "libSDL2_mixer-2.0.so.0": "libsdl2-mixer-2.0-0",
    "libpam.so.0": "libpam0g",
    "libpam_misc.so.0": "libpam0g",
    "libpamc.so.0": "libpam0g",
    "libnsl.so.2": "libnsl2",
    "libnsl.so.1": "libnsl1",
    "libtirpc.so.3": "libtirpc3",
    "libtirpc.so.1": "libtirpc1",
    "libuv.so.1": "libuv1",
    "libev.so.4": "libev4",
    "libevent-2.1.so.7": "libevent-2.1-7",
    "libevent_core-2.1.so.7": "libevent-core-2.1-7",
    "libevent_extra-2.1.so.7": "libevent-extra-2.1-7",
    "libevent_openssl-2.1.so.7": "libevent-openssl-2.1-7",
    "libevent_pthreads-2.1.so.7": "libevent-pthreads-2.1-7",
    "libcares.so.2": "libc-ares2",
    "libnghttp2.so.14": "libnghttp2-14",
    "libpsl.so.5": "libpsl5",
    "libssh2.so.1": "libssh2-1",
    "libssh.so.4": "libssh-4",
    "libkeyutils.so.1": "libkeyutils1",
    "libkrb5.so.3": "libkrb5-3",
    "libgssapi_krb5.so.2": "libgssapi-krb5-2",
    "libk5crypto.so.3": "libk5crypto3",
    "libcom_err.so.2": "libcom-err2",
    "libkrb5support.so.0": "libkrb5support0",
    "libldap.so.2": "libldap-2.5-0",
    "liblber.so.2": "libldap-2.5-0",
    "libsasl2.so.2": "libsasl2-2",
    "libkrb5.so.26": "libheimdal-krb5-2",
    "libnl-3.so.200": "libnl-3-200",
    "libnl-route-3.so.200": "libnl-route-3-200",
    "libnl-genl-3.so.200": "libnl-genl-3-200",
    "libnl-nf-3.so.200": "libnl-nf-3-200",
    "libnl-idiag-3.so.200": "libnl-idiag-3-200",
    "libjson-c.so.5": "libjson-c5",
    "libjson-c.so.4": "libjson-c4",
    "libjansson.so.4": "libjansson4",
    "libyaml-0.so.2": "libyaml-0-2",
    "libpipeline.so.1": "libpipeline1",
    "libdb-5.3.so": "libdb5.3",
    "libdb-5.1.so": "libdb5.1",
    "libdb-4.8.so": "libdb4.8",
    "liblmdb.so.0": "liblmdb0",
    "libxslt.so.1": "libxslt1.1",
    "libgssapi.so.3": "libgssapi3-heimdal",
    "libmagic.so.1": "libmagic1",
    "libmagic.so.6": "libmagic1",
    "libproc2.so.0": "libproc2-0",
    "libcryptsetup.so.12": "libcryptsetup12",
    "libdevmapper.so.1.02": "libdevmapper1.02.1",
    "libdevmapper-event.so.1.02": "libdevmapper-event1.02.1",
    "libmount.so.1": "libmount1",
    "libblkid.so.1": "libblkid1",
    "libuuid.so.1": "libuuid1",
    "libfdisk.so.1": "libfdisk1",
    "libsmartcols.so.1": "libsmartcols1",
    "libattr.so.1": "libattr1",
    "libacl.so.1": "libacl1",
    "libapparmor.so.1": "libapparmor1",
    "libpolkit-gobject-1.so.0": "libpolkit-gobject-1-0",
    "libpolkit-agent-1.so.0": "libpolkit-agent-1-0",
    "libtracker-sparql-3.0.so.0": "libtracker-sparql-3.0-0",
    "libgdk_pixbuf-2.0.so.0": "libgdk-pixbuf-2.0-0",
    "libgdk-3.so.0": "libgtk-3-0",
    "libgtk-3.so.0": "libgtk-3-0",
    "libgdk-2.so.0": "libgtk2.0-0",
    "libgtk-x11-2.0.so.0": "libgtk2.0-0",
    "libgailutil.so.18": "libgail18",
    "libgailutil-3.so.0": "libgail-3-0",
    "libatk-1.0.so.0": "libatk1.0-0",
    "libatk-bridge-2.0.so.0": "libatk-bridge2.0-0",
    "libatspi.so.0": "libatspi2.0-0",
    "libpango-1.0.so.0": "libpango-1.0-0",
    "libpangocairo-1.0.so.0": "libpangocairo-1.0-0",
    "libpangoft2-1.0.so.0": "libpangoft2-1.0-0",
    "libpangoxft-1.0.so.0": "libpangoxft-1.0-0",
    "libpangox-1.0.so.0": "libpangox-1.0-0",
    "libgvfscommon.so.0": "libgvfscommon0",
    "libgvfsdbus.so.0": "libgvfsdbus0",
    "libgusb.so.2": "libgusb2",
    "libudisks2.so.0": "libudisks2-0",
    "libnotify.so.4": "libnotify4",
    "libavahi-client.so.3": "libavahi-client3",
    "libavahi-common.so.3": "libavahi-common3",
    "libavahi-glib.so.1": "libavahi-glib1",
    "libcups.so.2": "libcups2",
    "liblttng-ust.so.0": "liblttng-ust0",
    "liblttng-ust-ctl.so.0": "liblttng-ust-ctl0",
    "libsensors.so.5": "libsensors5",
    "libnuma.so.1": "libnuma1",
    "libhwloc.so.15": "libhwloc15",
    "libhwloc.so.5": "libhwloc5",
    "libOpenCL.so.1": "ocl-icd-libopencl1",
    "libltdl.so.7": "libltdl7",
    "libsigsegv.so.2": "libsigsegv2",
    "libgsl.so.27": "libgsl27",
    "libgslcblas.so.0": "libgslcblas0",
}

BIN_MAP: Dict[str, str] = {
    "bash": "bash",
    "dash": "dash",
    "sh": "dash",
    "zsh": "zsh",
    "fish": "fish",
    "ksh": "ksh",
    "mksh": "mksh",
    "coreutils": "coreutils",
    "cat": "coreutils",
    "cp": "coreutils",
    "mv": "coreutils",
    "rm": "coreutils",
    "ln": "coreutils",
    "ls": "coreutils",
    "mkdir": "coreutils",
    "chmod": "coreutils",
    "chown": "coreutils",
    "dd": "coreutils",
    "df": "coreutils",
    "du": "coreutils",
    "echo": "coreutils",
    "env": "coreutils",
    "id": "coreutils",
    "kill": "coreutils",
    "pwd": "coreutils",
    "touch": "coreutils",
    "wc": "coreutils",
    "whoami": "coreutils",
    "uname": "coreutils",
    "sort": "coreutils",
    "tail": "coreutils",
    "head": "coreutils",
    "cut": "coreutils",
    "tr": "coreutils",
    "base64": "coreutils",
    "date": "coreutils",
    "mktemp": "coreutils",
    "realpath": "coreutils",
    "sleep": "coreutils",
    "tee": "coreutils",
    "test": "coreutils",
    "true": "coreutils",
    "false": "coreutils",
    "yes": "coreutils",
    "basename": "coreutils",
    "dirname": "coreutils",
    "md5sum": "coreutils",
    "sha256sum": "coreutils",
    "sha512sum": "coreutils",
    "cksum": "coreutils",
    "comm": "coreutils",
    "expand": "coreutils",
    "expr": "coreutils",
    "factor": "coreutils",
    "fmt": "coreutils",
    "fold": "coreutils",
    "groups": "coreutils",
    "hostid": "coreutils",
    "join": "coreutils",
    "link": "coreutils",
    "logname": "coreutils",
    "nl": "coreutils",
    "nohup": "coreutils",
    "nproc": "coreutils",
    "numfmt": "coreutils",
    "od": "coreutils",
    "paste": "coreutils",
    "pathchk": "coreutils",
    "pinky": "coreutils",
    "pr": "coreutils",
    "printenv": "coreutils",
    "printf": "coreutils",
    "ptx": "coreutils",
    "runcon": "coreutils",
    "seq": "coreutils",
    "shuf": "coreutils",
    "split": "coreutils",
    "stat": "coreutils",
    "stdbuf": "coreutils",
    "stty": "coreutils",
    "sum": "coreutils",
    "sync": "coreutils",
    "tac": "coreutils",
    "timeout": "coreutils",
    "truncate": "coreutils",
    "tsort": "coreutils",
    "tty": "coreutils",
    "unexpand": "coreutils",
    "uniq": "coreutils",
    "unlink": "coreutils",
    "users": "coreutils",
    "vdir": "coreutils",
    "who": "coreutils",
    "sed": "sed",
    "grep": "grep",
    "egrep": "grep",
    "fgrep": "grep",
    "rgrep": "grep",
    "awk": "gawk",
    "gawk": "gawk",
    "mawk": "mawk",
    "nawk": "gawk",
    "find": "findutils",
    "xargs": "findutils",
    "locate": "findutils",
    "updatedb": "findutils",
    "tar": "tar",
    "gzip": "gzip",
    "gunzip": "gzip",
    "zcat": "gzip",
    "xz": "xz-utils",
    "unxz": "xz-utils",
    "xzcat": "xz-utils",
    "bzip2": "bzip2",
    "bunzip2": "bzip2",
    "bzcat": "bzip2",
    "zip": "zip",
    "unzip": "unzip",
    "unrar": "unrar",
    "rar": "rar",
    "7z": "p7zip-full",
    "7za": "p7zip-full",
    "zstd": "zstd",
    "gcc": "gcc",
    "g++": "g++",
    "cc": "gcc",
    "c++": "g++",
    "cpp": "cpp",
    "gcc-ar": "gcc",
    "gcc-nm": "gcc",
    "gcc-ranlib": "gcc",
    "gcov": "gcc",
    "clang": "clang",
    "clang++": "clang",
    "ld.lld": "lld",
    "make": "make",
    "gmake": "make",
    "cmake": "cmake",
    "meson": "meson",
    "ninja": "ninja-build",
    "pkg-config": "pkg-config",
    "pkgconf": "pkg-config",
    "pkgconfig": "pkg-config",
    "ld": "binutils",
    "ld.bfd": "binutils",
    "as": "binutils",
    "ar": "binutils",
    "nm": "binutils",
    "objcopy": "binutils",
    "objdump": "binutils",
    "ranlib": "binutils",
    "readelf": "binutils",
    "size": "binutils",
    "strings": "binutils",
    "strip": "binutils",
    "addr2line": "binutils",
    "c++filt": "binutils",
    "dlltool": "binutils",
    "elfedit": "binutils",
    "gprof": "binutils",
    "nlmconv": "binutils",
    "windmc": "binutils",
    "windres": "binutils",
    "patch": "patch",
    "git": "git",
    "curl": "curl",
    "wget": "wget",
    "wget2": "wget2",
    "perl": "perl",
    "python3": "python3",
    "python": "python3",
    "ruby": "ruby",
    "lua": "lua5.4",
    "luajit": "luajit",
    "node": "nodejs",
    "nodejs": "nodejs",
    "php": "php",
    "php8": "php",
    "tclsh": "tcl",
    "wish": "tk",
    "vim": "vim",
    "nvim": "neovim",
    "nano": "nano",
    "vi": "vim",
    "emacs": "emacs",
    "emacsclient": "emacs",
    "micro": "micro",
    "helix": "helix",
    "less": "less",
    "more": "less",
    "most": "most",
    "sudo": "sudo",
    "doas": "doas",
    "pkexec": "policykit-1",
    "openssh": "openssh-client",
    "ssh": "openssh-client",
    "scp": "openssh-client",
    "sftp": "openssh-client",
    "ssh-keygen": "openssh-client",
    "ssh-keyscan": "openssh-client",
    "ssh-copy-id": "openssh-client",
    "rsync": "rsync",
    "rclone": "rclone",
    "systemctl": "systemd",
    "journalctl": "systemd",
    "loginctl": "systemd",
    "udevadm": "systemd",
    "systemd-analyze": "systemd",
    "systemd-resolve": "systemd",
    "systemd-tmpfiles": "systemd",
    "bootctl": "systemd",
    "hostnamectl": "systemd",
    "timedatectl": "systemd",
    "localectl": "systemd",
    "dbus-daemon": "dbus",
    "dbus-run-session": "dbus",
    "dbus-monitor": "dbus",
    "dbus-send": "dbus",
    "mount": "mount",
    "umount": "mount",
    "losetup": "mount",
    "swapon": "mount",
    "swapoff": "mount",
    "fdisk": "fdisk",
    "sfdisk": "fdisk",
    "cfdisk": "fdisk",
    "e2fsck": "e2fsprogs",
    "mkfs.ext2": "e2fsprogs",
    "mkfs.ext3": "e2fsprogs",
    "mkfs.ext4": "e2fsprogs",
    "mke2fs": "e2fsprogs",
    "tune2fs": "e2fsprogs",
    "resize2fs": "e2fsprogs",
    "debugfs": "e2fsprogs",
    "badblocks": "e2fsprogs",
    "dumpe2fs": "e2fsprogs",
    "mkfs.btrfs": "btrfs-progs",
    "btrfs": "btrfs-progs",
    "mkfs.xfs": "xfsprogs",
    "xfs_repair": "xfsprogs",
    "mkfs.fat": "dosfstools",
    "fsck": "util-linux",
    "ip": "iproute2",
    "ss": "iproute2",
    "tc": "iproute2",
    "bridge": "iproute2",
    "rtmon": "iproute2",
    "nstat": "iproute2",
    "lnstat": "iproute2",
    "ifstat": "iproute2",
    "devlink": "iproute2",
    "iptables": "iptables",
    "ip6tables": "iptables",
    "nft": "nftables",
    "iptables-nft": "nftables",
    "ping": "iputils-ping",
    "ping6": "iputils-ping",
    "traceroute": "traceroute",
    "netstat": "net-tools",
    "ifconfig": "net-tools",
    "route": "net-tools",
    "arp": "net-tools",
    "iwconfig": "wireless-tools",
    "iw": "iw",
    "wpa_supplicant": "wpasupplicant",
    "host": "bind9-host",
    "dig": "bind9-host",
    "nslookup": "bind9-host",
    "ncat": "nmap",
    "nmap": "nmap",
    "tcpdump": "tcpdump",
    "wireshark": "wireshark",
    "tshark": "tshark",
    "mtr": "mtr",
    "socat": "socat",
    "netcat": "netcat-openbsd",
    "nc": "netcat-openbsd",
    "hostname": "hostname",
    "dmesg": "util-linux",
    "lsblk": "util-linux",
    "blkid": "util-linux",
    "findmnt": "util-linux",
    "logger": "util-linux",
    "flock": "util-linux",
    "whereis": "util-linux",
    "su": "util-linux",
    "login": "util-linux",
    "agetty": "util-linux",
    "hwclock": "util-linux",
    "renice": "util-linux",
    "ionice": "util-linux",
    "chcpu": "util-linux",
    "lscpu": "util-linux",
    "lslocks": "util-linux",
    "lsns": "util-linux",
    "lsof": "lsof",
    "neofetch": "neofetch",
    "fastfetch": "fastfetch",
    "ps": "procps",
    "top": "procps",
    "htop": "htop",
    "btop": "btop",
    "free": "procps",
    "killall": "procps",
    "pidof": "procps",
    "pmap": "procps",
    "pwdx": "procps",
    "slabtop": "procps",
    "tload": "procps",
    "vmstat": "procps",
    "watch": "procps",
    "pgrep": "procps",
    "pkill": "procps",
    "skill": "procps",
    "snice": "procps",
    "sysctl": "procps",
    "uptime": "procps",
    "w": "procps",
    "dpkg": "dpkg",
    "dpkg-deb": "dpkg",
    "dpkg-query": "dpkg",
    "apt": "apt",
    "apt-get": "apt",
    "apt-cache": "apt",
    "apt-mark": "apt",
    "pacman": "pacman",
    "pacman-key": "pacman",
    "pamac": "pamac",
    "yay": "yay",
    "paru": "paru",
    "rpm": "rpm",
    "rpmbuild": "rpm",
    "dnf": "dnf",
    "yum": "yum",
    "zypper": "zypper",
    "apk": "apk-tools",
    "emerge": "emerge",
    "eix": "eix",
    "equery": "gentoolkit",
    "xbps-install": "xbps",
    "xbps-query": "xbps",
    "xbps-remove": "xbps",
    "nix-env": "nix",
    "nixos-rebuild": "nix",
    "guix": "guix",
    "flatpak": "flatpak",
    "snap": "snapd",
    "init": "init",
    "openrc": "openrc",
    "rc-update": "openrc",
    "rc-service": "openrc",
    "runit": "runit",
    "sv": "runit",
    "s6-svscan": "s6",
    "s6-svc": "s6",
    "supervisord": "supervisor",
    "cron": "cron",
    "crond": "cronie",
    "at": "at",
    "anacron": "anacron",
    "syslog-ng": "syslog-ng",
    "rsyslogd": "rsyslog",
    "logrotate": "logrotate",
    "screen": "screen",
    "tmux": "tmux",
    "byobu": "byobu",
    "uname": "coreutils",
    "arch": "coreutils",
    "getconf": "libc-bin",
    "locale": "locales",
    "localectl": "systemd",
    "timedatectl": "systemd",
    "loginctl": "systemd",
    "machinectl": "systemd",
    "busctl": "systemd",
    "coredumpctl": "systemd",
    "resolvconf": "resolvconf",
    "aa-status": "apparmor",
    "aa-enforce": "apparmor-utils",
    "sestatus": "policycoreutils",
    "getenforce": "policycoreutils",
    "firewalld": "firewalld",
    "ufw": "ufw",
    "iptables": "iptables",
    "acpid": "acpid",
    "cpupower": "linux-cpupower",
    "tuned": "tuned",
    "irqbalance": "irqbalance",
    "haveged": "haveged",
    "rngd": "rng-tools",
    "lsusb": "usbutils",
    "lspci": "pciutils",
    "lsmod": "kmod",
    "modprobe": "kmod",
    "insmod": "kmod",
    "rmmod": "kmod",
    "depmod": "kmod",
    "sestatus": "policycoreutils",
    "chcon": "coreutils",
    "restorecon": "policycoreutils",
    "setenforce": "policycoreutils",
    "semanage": "policycoreutils-python-utils",
    "auditctl": "auditd",
    "ausearch": "auditd",
    "aureport": "auditd",
    "last": "util-linux",
    "lastb": "util-linux",
    "faillog": "util-linux",
    "wall": "util-linux",
    "write": "util-linux",
    "mesg": "util-linux",
    "xrandr": "x11-xserver-utils",
    "xset": "x11-xserver-utils",
    "xdpyinfo": "x11-utils",
    "xwininfo": "x11-utils",
    "xprop": "x11-utils",
    "xdotool": "xdotool",
    "xclip": "xclip",
    "xsel": "xsel",
    "wl-paste": "wl-clipboard",
    "wl-copy": "wl-clipboard",
    "wayland-info": "wayland-utils",
    "swaymsg": "sway",
    "hyprctl": "hyprland",
    "i3status": "i3status",
    "i3-msg": "i3",
    "openbox": "openbox",
    "fluxbox": "fluxbox",
    "bspc": "bspwm",
    "bspwm": "bspwm",
    "awesome": "awesome",
    "qtile": "qtile",
    "dwm": "dwm",
    "st": "stterm",
    "alacritty": "alacritty",
    "kitty": "kitty",
    "wezterm": "wezterm",
    "foot": "foot",
    "urxvt": "rxvt-unicode",
    "xterm": "xterm",
    "gnome-terminal": "gnome-terminal",
    "konsole": "konsole",
}

FOREIGN_LOCKS: Dict[str, List[str]] = {
    "pacman": ["/var/lib/pacman/db.lck"],
    "dpkg": ["/var/lib/dpkg/lock", "/var/lib/dpkg/lock-frontend"],
    "apt": ["/var/lib/apt/lists/lock", "/var/lib/apt/lock", "/var/cache/apt/archives/lock"],
    "rpm": ["/var/lib/rpm/.rpm.lock", "/var/lib/rpm/__db.001"],
    "dnf": ["/var/run/dnf.lock", "/var/lock/dnf.lock"],
    "zypper": ["/var/run/zypper.lock"],
    "apk": ["/var/lib/apk/lock"],
    "emerge": ["/var/cache/edb/lock", "/var/lib/portage/.lock"],
    "xbps": ["/var/run/xbps.lock"],
    "nix": ["/nix/var/nix/db/lock"],
    "snap": ["/var/lib/snapd/lock"],
    "flatpak": ["/var/lib/flatpak/.lock"],
}


def get_foreign_pm_files() -> Dict[str, Set[str]]:
    result: Dict[str, Set[str]] = {}

    pacman_files = _get_pacman_files()
    if pacman_files:
        result["pacman"] = pacman_files

    rpm_files = _get_rpm_files()
    if rpm_files:
        result["rpm"] = rpm_files

    apk_files = _get_apk_files()
    if apk_files:
        result["apk"] = apk_files

    return result


def _get_pacman_files() -> Set[str]:
    files: Set[str] = set()
    local_db = Path("/var/lib/pacman/local")
    if not local_db.is_dir():
        return files
    for pkg_dir in local_db.iterdir():
        if not pkg_dir.is_dir():
            continue
        file_list = pkg_dir / "files"
        if file_list.is_file():
            try:
                content = file_list.read_text(errors="replace")
                for line in content.split("\n"):
                    if line and not line.startswith("%") and not line.endswith("/"):
                        files.add("/" + line.strip("/"))
            except Exception:
                pass
    return files


def _get_rpm_files() -> Set[str]:
    files: Set[str] = set()
    try:
        proc = subprocess.run(
            ["rpm", "-ql", "--all"],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line and os.path.isfile(line):
                    files.add(line)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return files


def _get_apk_files() -> Set[str]:
    files: Set[str] = set()
    try:
        proc = subprocess.run(
            ["apk", "info", "-l"],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line and os.path.isfile(line):
                    files.add(line)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return files


_HAS_SEEDED = False
_CACHED: Dict[str, str] = {}


def detect_host_packages() -> Dict[str, str]:
    global _HAS_SEEDED, _CACHED

    if os.environ.get("TANUKI_IGNORE_HOST"):
        return {}

    if _HAS_SEEDED:
        return _CACHED

    result: Dict[str, str] = {}

    ldconfig_map = _get_ldconfig_paths()
    search_paths = get_library_search_paths()

    for lib_name, debian_name in LIB_MAP.items():
        found = False
        ldconfig_path = ldconfig_map.get(lib_name)
        if ldconfig_path and os.path.exists(ldconfig_path):
            result[debian_name] = HOST_VERSION_SENTINEL
            continue
        for search_path in search_paths:
            if (Path(search_path) / lib_name).is_file():
                result[debian_name] = HOST_VERSION_SENTINEL
                found = True
                break

    for bin_name, debian_name in BIN_MAP.items():
        if shutil.which(bin_name) and debian_name not in result:
            result[debian_name] = HOST_VERSION_SENTINEL

    _HAS_SEEDED = True
    _CACHED = result
    return result
