"""Render the README screens: run shaker in a pseudo terminal and save what it prints as SVG.

    .venv/bin/python docs/make_screens.py

Needs a scratch campaign, which it creates in a temporary directory.
"""

import fcntl
import os
import pty
import re
import struct
import sys
import tempfile
import termios
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import TerminalTheme
from rich.text import Text

DOCS = Path(__file__).resolve().parent
SHAKER = str(Path(sys.executable).parent / "shaker")
WIDTH = 86
# Light terminal: warm paper background, the light variant of the SHAKER palette.
THEME = TerminalTheme(
    (251, 248, 245), (42, 33, 27),
    [(42, 33, 27), (196, 40, 58), (27, 122, 62), (138, 90, 0),
     (194, 65, 12), (190, 24, 93), (180, 83, 9), (107, 98, 90)],
)


def capture(args, cwd):
    """Run a command in a pseudo terminal of fixed width and return its raw output."""
    pid, fd = pty.fork()
    size = struct.pack("HHHH", 50, WIDTH, 0, 0)
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, size)
        os.chdir(cwd)
        os.environ.update(TERM="xterm-256color", COLORTERM="truecolor",
                          SHAKER_NO_ANIMATION="1", SHAKER_NO_QUOTES="1",
                          SHAKER_THEME="light", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
        os.execv(SHAKER, [SHAKER, *args])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
    out = b""
    while True:
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        out += data
    os.waitpid(pid, 0)
    text = out.decode("utf-8", "replace").replace("\r\n", "\n")
    text = re.sub(r"\x1b\[\?[0-9;]*[hl]", "", text)  # cursor and synchronized-output switches
    return text.rstrip("\n")


def save(name, title, text):
    console = Console(record=True, width=WIDTH, file=open(os.devnull, "w"),
                      force_terminal=True, color_system="truecolor")
    console.print(Text.from_ansi(text))
    console.save_svg(str(DOCS / name), title=title, theme=THEME)
    print("wrote", DOCS / name)


def main():
    with tempfile.TemporaryDirectory() as scratch:
        save("screen-help.svg", "shaker", capture([], scratch))
        capture(["init"], scratch)
        save("screen-plan.svg", "shaker plan", capture(["plan"], scratch))
        save("screen-lipids.svg", "shaker lipids show POPG",
             capture(["lipids", "show", "POPG"], scratch))


if __name__ == "__main__":
    main()
