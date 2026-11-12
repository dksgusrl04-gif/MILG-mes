"""Make engine tests and their subprocesses importable from the repository root."""
import os
import sys
from pathlib import Path

AI_ROOT=str(Path(__file__).resolve().parents[1])
if AI_ROOT not in sys.path:sys.path.insert(0,AI_ROOT)
paths=os.environ.get('PYTHONPATH','').split(os.pathsep)
if AI_ROOT not in paths:os.environ['PYTHONPATH']=os.pathsep.join([AI_ROOT,*filter(None,paths)])
