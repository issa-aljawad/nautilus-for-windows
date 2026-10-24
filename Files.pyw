"""Double-click launcher (runs without a console window)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from files_win.__main__ import main

main()
