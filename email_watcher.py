"""Email watcher entry point named as on the Command Center page.

    python email_watcher.py [--loop 30]
"""
import sys

from jobhunt.cli import main

sys.exit(main(["watch-email", *sys.argv[1:]]))
