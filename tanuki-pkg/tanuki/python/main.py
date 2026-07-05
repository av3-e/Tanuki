#!/usr/bin/env python3

import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))

from cli.commands import Commands
from cli.output import print_help, print_version
from lua_bridge.bridge import LuaBridge

def _parse_flags(args: list) -> dict:
    flags = {"dry_run": False, "download_only": False, "ignore_deps": False,
             "force": False, "root": None, "with_recommends": False, "files": False}
    pkgs = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dry-run":
            flags["dry_run"] = True
        elif a == "--download-only":
            flags["download_only"] = True
        elif a == "--ignore-deps":
            flags["ignore_deps"] = True
        elif a == "--force":
            flags["force"] = True
        elif a == "--with-recommends":
            flags["with_recommends"] = True
        elif a == "--files":
            flags["files"] = True
        elif a == "--root":
            i += 1
            if i < len(args):
                flags["root"] = args[i]
            else:
                print("--root requires a path argument", file=sys.stderr)
                sys.exit(1)
        else:
            pkgs.append(a)
        i += 1
    return flags, pkgs

def main():
    args = sys.argv[1:]

    if not args or args[0] in ['-h', '--help']:
        print_help()
        return 0

    if args[0] in ['-v', '--version']:
        print_version()
        return 0

    config_path = Path(os.environ.get('TANUKI_CONFIG', '/var/lib/tanuki/config.lua'))
    lua = LuaBridge(config_path)
    config = lua.load_config()

    flags, cmd_args = _parse_flags(args[1:])
    command = args[0]

    if flags["root"]:
        config["root"] = flags["root"]

    cmd = Commands(config)

    try:
        if command in ['install', 'i', '-I', '--install']:
            cmd.install(cmd_args, dry_run=flags["dry_run"],
                        download_only=flags["download_only"],
                        ignore_deps=flags["ignore_deps"],
                        force=flags["force"],
                        with_recommends=flags["with_recommends"])
        elif command in ['remove', 'rm']:
            cmd.remove(cmd_args)
        elif command in ['update', 'up', '-u', '--update']:
            cmd.update()
        elif command in ['upgrade', '-U']:
            cmd.upgrade(force=flags["force"])
        elif command in ['list', 'ls']:
            cmd.list_packages(show_files=flags["files"],
                              pkg_name=cmd_args[0] if cmd_args else None)
        elif command in ['files', 'fl']:
            cmd.list_packages(show_files=True,
                              pkg_name=cmd_args[0] if cmd_args else None)
        elif command == 'verify':
            cmd.verify(cmd_args[0] if cmd_args else None)
        elif command in ['undo', 'rollback']:
            cmd.undo()
        elif command == 'search':
            cmd.search(cmd_args[0] if cmd_args else '')
        elif command == 'info':
            cmd.info(cmd_args[0] if cmd_args else '')
        elif command == 'provides':
            cmd.provides(cmd_args[0] if cmd_args else '')
        elif command in ['reinstall', 're', '-r', '--reinstall']:
            cmd.reinstall(cmd_args[0] if cmd_args else '')
        elif command in ['purge', 'p', '-p', '--purge']:
            cmd.purge(cmd_args[0] if cmd_args else '')
        elif command == 'clean':
            cmd.clean()
        elif command == 'autoclean':
            cmd.autoclean()
        elif command == 'autoremove':
            cmd.autoremove()
        elif command == 'init':
            cmd.init()
        elif command == 'snapshot':
            cmd.snapshot(cmd_args)
        else:
            print(f"Unknown command: {command}")
            print("Use 'tanuki --help' for usage")
            return 1
    except KeyboardInterrupt:
        print("\nOperation cancelled")
        return 130

    return 0

if __name__ == '__main__':
    sys.exit(main())
