"""Server entry: same as python scripts/run_once.py"""
import os
import runpy
import sys

_root = os.path.dirname(os.path.abspath(__file__))
os.chdir(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)
runpy.run_path(os.path.join(_root, "scripts", "run_once.py"), run_name="__main__")
