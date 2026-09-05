"""Frozen React service entry; accidental Tk imports are defects, not fallbacks."""
import importlib.abc
import sys


class NoTk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"tkinter", "_tkinter"} or fullname in {"PIL.ImageTk", "PIL._imagingtk"}:
            raise ImportError("Tkinter is not part of the React SDK")


sys.meta_path.insert(0, NoTk())

from allin1_sdk.desktop_sidecar_host import main

if __name__ == "__main__":
    main()
