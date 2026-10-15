import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "omission_inventory"), str(HERE.parent.parent / "scripts"), str(HERE.parent.parent)]
