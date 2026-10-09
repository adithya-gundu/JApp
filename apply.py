"""Auto-apply entry point named as on the Command Center page.

    python apply.py <application-form-url> [--submit]
"""
import sys

from jobhunt.cli import main

sys.exit(main(["apply", *sys.argv[1:]]))
