import sys, os
from pathlib import Path

_THIS = Path(__file__).resolve().parent.parent

_python_path = str(_THIS / "python")
_tanuki_path = str(_THIS / "tanuki" / "python")

if _python_path not in sys.path:
    sys.path.insert(0, _python_path)
if _tanuki_path not in sys.path:
    sys.path.insert(0, _tanuki_path)

def entry_point():
    from main import main
    sys.exit(main())

if __name__ == "__main__":
    entry_point()
