# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Reading one ``Bash`` call as the commands it runs.

Not a shell parser: a walk over the line that knows quotes, backslashes, ``$(...)`` / backticks,
``&&``, ``||``, ``;``, ``&`` and newlines, and peels the compound-command syntax (``for``, ``if``,
``case``, ``( ... )``) and the wrappers (``sudo``, ``timeout 300``, ``nice -n 10``, ``VAR=1``,
``uv run``) off a command to find the program and the file it writes. Good for the lines agents
write; a line it cannot read gives a step of its own, never an exception.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

#: Separators between commands, and what each says about the status a call reports: ``a && b`` fails
#: as a whole when ``a`` does; after ``;``, a newline, ``||`` or ``&`` the status is a later command's.
HIDING = {";", "\n", "||", "&"}

_OPERATORS = {"|", "||", "&&", ";", "&", ">", ">>", ">|", ">&", "<", "<<", "&>", "&>>", "(", ")"}
_SHELL_WRITERS = {"sed", "perl", "echo", "printf", "cat", "tee"}  # their output written to a file is an edit
_KEYWORDS = r"(?:if|then|else|elif|do|while|until|!|\{)"
_NO_COMMAND = re.compile(r"^(?:for|select|fi|done|esac|\}|in|function)\b")
# `-i`, `-i.bak`, `-ni`, `--in-place`: the in-place flag by its tool's own option letters, so that
# `perl -Mstrict` is not an edit.
_IN_PLACE = {"sed": re.compile(r"^(-[nEsrzu]*i[^-\s]*|--in-place(=.*)?)$"),
             "perl": re.compile(r"^-[pnalw0-9]*i[^-\s]*$")}


class _Scan:
    """The state of the walk over a shell line: the quote open, the ``$(...)`` depth, a backtick."""

    def __init__(self, command: str):
        self.text, self.i = command, 0
        self.cur: list[str] = []
        self.quote, self.depth, self.tick = "", 0, False

    def eat(self, n: int = 1, keep: bool = True) -> None:
        if keep:
            self.cur.append(self.text[self.i:self.i + n])
        self.i += n

    def step(self) -> str:
        """Consume the next piece; return the separator it was, if it was one."""
        ch, nxt = self.text[self.i], self.text[self.i + 1:self.i + 2]
        if self.quote:
            if ch == "\\" and self.quote == '"' and nxt:
                self.eat(2)
            else:
                self.quote = "" if ch == self.quote else self.quote
                self.eat()
        elif ch == "\\":  # a continued line is dropped; an escaped character is a word character
            self.eat(2, keep=nxt != "\n")
        elif ch in "'\"`" or (ch == "$" and nxt == "(") or (self.depth and ch in "()"):
            self.open_close(ch, nxt)
        elif ch == "#" and not (self.depth or self.tick) and (not self.cur or self.cur[-1].isspace()):
            while self.i < len(self.text) and self.text[self.i] != "\n":  # a comment runs to the end of the line
                self.i += 1
        else:
            sep = "" if self.depth or self.tick else _separator(self.text, self.i)
            if sep:
                self.i += len(sep)
                return sep
            self.eat()
        return ""

    def open_close(self, ch: str, nxt: str) -> None:
        """A quote, a backtick, ``$(`` or a parenthesis inside ``$(...)``: track it, keep it."""
        if ch in "'\"":
            self.quote = ch
        elif ch == "`":
            self.tick = not self.tick
        elif ch == "$":
            self.depth += 1
            self.eat(2)
            return
        else:
            self.depth += 1 if ch == "(" else -1
        self.eat()


def split_commands(command: str) -> list[tuple[str, str]]:
    """``(command, separator after it)`` for each command of a shell line.

    Heredoc bodies and comments are dropped (their lines are data); a separator inside quotes,
    after a backslash, or inside ``$(...)`` / backticks does not split."""
    command = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n\s*\1\s*(?=\n|$)", r"<<\1\2", command, flags=re.S)
    out: list[tuple[str, str]] = []
    scan = _Scan(command)
    while scan.i < len(command):
        sep = scan.step()
        if sep:
            out.append(("".join(scan.cur), sep))
            scan.cur = []
    out.append(("".join(scan.cur), ""))
    return [(seg.strip(), sep) for seg, sep in out if seg.strip() and not seg.strip().startswith("#")]


def _separator(command: str, i: int) -> str:
    ch, nxt, prev = command[i], command[i + 1:i + 2], command[i - 1:i] if i else ""
    if ch in ";\n":
        return ch
    if command[i:i + 2] in ("&&", "||"):
        return command[i:i + 2]
    if ch == "&" and nxt != ">" and prev not in (">", "<", "&"):  # not `&>`, `2>&1`, `<&3`
        return "&"
    return ""


def segments(command: str) -> list[str]:
    return [seg for seg, _ in split_commands(command)]


def tokens(segment: str) -> list[str]:
    """Shell words with unquoted operators (``|``, ``>``, ``>>``, ``2>&1``) as their own tokens, so a
    ``>`` or ``|`` inside quotes stays part of a word."""
    lex = shlex.shlex(_mask(segment), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return segment.split()


def _mask(text: str) -> str:
    """``text`` with each ``$(...)`` and backtick substitution replaced by one word, so its inner
    spaces, ``;`` and ``&&`` are not read as this command's."""
    out, i, depth, quote = [], 0, 0, ""
    while i < len(text):
        ch = text[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"" and not depth:
            quote = ch
        if not quote and ch == "$" and text[i + 1:i + 2] == "(":
            j, d = i + 2, 1
            while j < len(text) and d:
                d += (text[j] == "(") - (text[j] == ")")
                j += 1
            out.append("SUBST")
            i = j
            continue
        if not quote and ch == "`":
            j = text.find("`", i + 1)
            j = len(text) - 1 if j < 0 else j
            out.append("SUBST")
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def clean(segment: str) -> str:
    """The command of a segment without the compound-command syntax around it:
    ``do black $f`` -> ``black $f``, ``(cd sub`` -> ``cd sub``, ``pytest)`` -> ``pytest``, a case label
    and its ``case ... in`` header dropped; a ``for`` header, ``fi``, ``done`` leave nothing."""
    text = segment.strip()
    while True:
        before = text
        text = re.sub(rf"^(?:\(+|{_KEYWORDS}(?=\s|$))\s*", "", text)
        text = re.sub(r"^case\b.*?\bin\b\s*", "", text) if text.startswith("case ") else text
        text = re.sub(r"^\(?[^\s()]+\)(?=\s+\S)\s*", "", text)  # a `case` label followed by its command
        if text == before:
            break
    if _NO_COMMAND.match(text) or text.startswith("case "):
        return ""
    while text.endswith(")") and text.count(")") > text.count("("):
        text = text[:-1].rstrip()
    return text


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(_mask(clean(segment)))
    except ValueError:
        return clean(segment).split()


_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")


def _wrapper_length(words: list[str]) -> int:
    """How many leading words only wrap the program (0: ``words[0]`` is the program)."""
    head = Path(words[0]).name
    if re.match(r"^\w+=", words[0]) or head in ("exec", "time", "nohup", "command", "builtin"):
        return 1
    if head in ("sudo", "env", "nice"):
        n = 1
        while n < len(words) and (words[n].startswith("-") or (head == "env" and re.match(r"^\w+=", words[n]))):
            n += 2 if head == "nice" and words[n] == "-n" else 1
        return n
    if head == "timeout":
        n = 1
        while n < len(words) and words[n].startswith("-"):
            n += 2 if words[n] in ("-k", "-s") else 1
        return n + (n < len(words) and bool(_DURATION.match(words[n])))
    if words[:2] == ["uv", "run"] or (len(words) >= 3 and re.match(r"^python[\d.]*$", head) and words[1] == "-m"):
        return 2
    return 0


def strip_wrappers(words: list[str]) -> list[str]:
    """``words`` past what only wraps the program: ``VAR=1``, ``sudo``, ``env``, ``time``, ``nohup``,
    ``nice -n 10``, ``timeout 300``, ``uv run``, ``python -m``."""
    words = list(words)
    while words and (n := _wrapper_length(words)):
        del words[:n]
    return words


def program(command: str) -> str:
    """The program a shell line runs: past ``cd x &&``, wrappers and compound-command syntax."""
    for seg in segments(command):
        words = strip_wrappers(_words(seg))
        if not words or words[0] in ("cd", "pushd", "source", ".", "export", "set", "[", "[["):
            continue
        return Path(words[0]).name
    return ""


def step_text(segment: str) -> str:
    """The command of a segment as the agent wrote it, without the compound-command syntax."""
    return clean(segment) or segment.strip()


def written(segment: str) -> str:
    """The file a shell command writes, if it is one of the plain ways to write one."""
    words = tokens(clean(segment))
    stages: list[list[str]] = [[]]
    for w in words:
        stages.append([]) if w == "|" else stages[-1].append(w)
    for stage in stages:
        if target := _stage_writes(strip_wrappers(stage)):
            return target
    for i, w in enumerate(words[:-1]):
        if w in (">", ">>", ">|") and not (i and _fd_redirect(segment, words[i - 1])):
            target = words[i + 1]
            if target not in _OPERATORS and target != "/dev/null":
                return target
    return ""


def _stage_writes(stage: list[str]) -> str:
    """The file an in-place edit (``sed -i f``) or ``tee f`` writes, in one stage of a pipeline."""
    if not stage:
        return ""
    in_place = _IN_PLACE.get(Path(stage[0]).name)
    if in_place and any(in_place.match(w) for w in stage[1:]):
        files = [w for w in stage[1:] if not w.startswith("-") and w not in _OPERATORS]
        if len(files) >= 2:
            return files[-1]
    if stage[0] == "tee":
        rest = [x for x in stage[1:] if not x.startswith("-")]
        if rest and rest[0] not in _OPERATORS:
            return rest[0]
    return ""


def _fd_redirect(segment: str, prev: str) -> bool:
    """``2>`` / ``2>>`` (a digit glued to the operator) redirects another stream; ``echo 3 > f`` writes."""
    return prev.isdigit() and prev != "1" and re.search(rf"(?<!\S){prev}>", segment) is not None


def writes_file(program_name: str) -> bool:
    """Whether a program's saved output counts as an edit when it names a file (``cat > f``, not ``pytest > log``)."""
    return program_name in _SHELL_WRITERS
