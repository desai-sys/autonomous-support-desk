"""
Static checks on the files the model wrote.

Nothing here runs the generated code. The checks look for the ways generated
code usually fails: cut off mid-file, syntax errors, placeholders instead of
code, packages that aren't installed, a missing entry function, or a page that
calls an API the app doesn't have.

Each problem is a (severity, message) pair. "error" must be fixed before the app
is offered for approval. "soft" is sent back to the model once, but doesn't
block if it remains, because the JavaScript check is a heuristic.
"""

import ast
import re
import sys
from html.parser import HTMLParser
from typing import List, Optional, Tuple

Problem = Tuple[str, str]
MAX_FILE_BYTES = 200_000
ALLOWED_PACKAGES = {"boto3", "botocore"}
_PLACEHOLDER = re.compile(
    r"(rest of (the )?(code|logic|implementation|game|script|function)|(remaining|existing) "
    r"(code|logic|implementation)|your (code|logic) here|implement(ation)? (goes )?here|"
    r"add (your|more) (code|logic) here|same as (before|above)\b.{0,20}$|\.\.\. ?\(?(rest|more|omitted|unchanged))",
    re.I | re.M)
_REF_TAGS = {"script": "src", "link": "href", "img": "src", "audio": "src", "source": "src"}
_DATA_SCRIPT_TYPES = ("application/json", "application/ld+json", "text/template")
_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = ("return", "typeof", "case", "in", "of", "do", "else", "void", "delete", "throw", "new")


def check_file(path: str, content: str, blueprint: str) -> List[Problem]:
    problems: List[Problem] = []
    if not content or not content.strip():
        return [("error", f"{path} is empty.")]
    if len(content.encode()) > MAX_FILE_BYTES:
        return [("error", f"{path} is larger than {MAX_FILE_BYTES // 1000} KB; make it smaller.")]
    hit = _PLACEHOLDER.search(content)
    if hit:
        problems.append(("error", f"{path} contains a placeholder instead of real code "
                                  f"(\"{hit.group(0).strip()[:60]}\"). Write every part in full."))
    if path.endswith(".py"):
        problems += _check_python(path, content)
    elif path.endswith(".html"):
        problems += _check_html(path, content, blueprint)
    return problems


# ── Python ────────────────────────────────────────────────────────────────────

def _check_python(path: str, source: str) -> List[Problem]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [("error", f"{path} has a Python syntax error on line {e.lineno}: {e.msg}.")]
    problems: List[Problem] = []
    stdlib = sys.stdlib_module_names
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                problems.append(("error", f"{path} uses a relative import; this app is a single module."))
                continue
            modules = [node.module or ""]
        for module in modules:
            top = module.split(".")[0]
            if top in ("handler", "api", "job"):
                problems.append(("error", f"{path} imports '{top}'; the platform calls your function, "
                                          f"don't import the entry point or other app modules."))
            elif top not in stdlib and top not in ALLOWED_PACKAGES:
                problems.append(("error", f"{path} imports '{top}', which is not installed. Only the Python "
                                          f"standard library and boto3 are available (use urllib.request "
                                          f"for HTTP calls)."))
    name, arity = ("handle", 5) if path.endswith("api.py") else ("run", 1)
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    if name not in functions:
        signature = "handle(method, path, query, body, store)" if name == "handle" else "run(store)"
        problems.append(("error", f"{path} must define a top-level function `def {signature}`."))
    else:
        args = functions[name].args
        if len(args.posonlyargs) + len(args.args) != arity or args.vararg or args.kwonlyargs:
            problems.append(("error", f"`{name}` in {path} must take exactly {arity} "
                                      f"parameter{'s' if arity > 1 else ''}."))
    return problems


# ── HTML and inline JavaScript ───────────────────────────────────────────────

class _Page(HTMLParser):
    """Collects what the checks need from a page, using the real HTML parser."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: set = set()
        self.closed: set = set()
        self.refs: List[str] = []
        self.scripts: List[str] = []          # inline JavaScript
        self.unclosed: List[str] = []         # <script>/<style> that never end
        self._raw: Optional[str] = None       # the raw-text element we are inside
        self._inline = False
        self._buffer: List[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = {k: (v or "") for k, v in attrs}
        self.tags.add(tag)
        attribute = _REF_TAGS.get(tag)
        if attribute and attributes.get(attribute):
            self.refs.append(attributes[attribute].strip())
        if tag in ("script", "style"):
            self._raw, self._buffer = tag, []
            self._inline = (tag == "script" and "src" not in attributes
                            and attributes.get("type", "").lower() not in _DATA_SCRIPT_TYPES)

    def handle_data(self, data):
        if self._raw:
            self._buffer.append(data)

    def handle_endtag(self, tag):
        self.closed.add(tag)
        if tag == self._raw:
            if tag == "script" and self._inline:
                self.scripts.append("".join(self._buffer))
            self._raw = None

    def finish(self):
        self.close()
        if self._raw:
            self.unclosed.append(self._raw)


def _check_html(path: str, html: str, blueprint: str) -> List[Problem]:
    problems: List[Problem] = []
    page = _Page()
    page.feed(html)
    page.finish()
    if not {"html", "body"} <= page.tags:
        problems.append(("error", f"{path} must be a complete HTML document with <html> and <body>."))
    for tag in page.unclosed:
        problems.append(("error", f"{path} has a <{tag}> block that is never closed (the file is cut off "
                                  f"or malformed)."))
    if not page.unclosed and not {"html", "body"} <= page.closed:
        problems.append(("error", f"{path} is cut off: it has no closing </body></html>. Write the whole file; "
                                  f"if it is too long, simplify the app."))
    for ref in page.refs:
        if not re.match(r"^(https?:)?//|^data:|^mailto:|^#|^/api/", ref):
            problems.append(("error", f"{path} loads '{ref[:60]}', a separate file that doesn't exist. "
                                      f"Put all CSS and JavaScript inline in this one file."))
    if blueprint == "static-site" and re.search(r"""(fetch|XMLHttpRequest)[^;\n]{0,80}["'`]/api/""", html):
        problems.append(("error", f"{path} calls /api/, but this app has no server-side code. Keep all logic "
                                  f"in the browser and use localStorage to remember things."))
    for script in page.scripts:
        issue = javascript_issue(script)
        if issue:
            problems.append(("soft", f"The JavaScript in {path} looks malformed: {issue}"))
            break
    return problems


def javascript_issue(source: str) -> str:
    """
    A description of an unbalanced bracket or unterminated string in JavaScript, or ''.
    A heuristic scanner (strings, template literals, comments and regular expressions are
    skipped), meant to catch truncated or mangled code, not to validate the language.
    """
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: List[Tuple[str, int]] = []       # (opener or "${", line)
    i, line, n = 0, 1, len(source)
    last = ""                                # last significant character
    word = ""                                # last identifier, for regex detection
    while i < n:
        c = source[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        two = source[i:i + 2]
        if two == "//":
            end = source.find("\n", i)
            i = n if end == -1 else end
            continue
        if two == "/*":
            end = source.find("*/", i + 2)
            if end == -1:
                return f"a /* comment starting on line {line} is never closed."
            line += source.count("\n", i, end)
            i = end + 2
            continue
        if c in "'\"":
            start, i = line, i + 1
            while i < n and source[i] != c:
                if source[i] == "\\":
                    i += 1
                elif source[i] == "\n":
                    return f"a string starting on line {start} is not closed before the end of the line."
                i += 1
            if i >= n:
                return f"a string starting on line {start} is never closed."
            i += 1
            last, word = c, ""
            continue
        if c == "`":
            stack.append(("`", line))
            i += 1
            i, line, problem = _template(source, i, line, stack)
            if problem:
                return problem
            last, word = "`", ""
            continue
        if c == "/" and (last == "" or last in _REGEX_PRECEDERS or word in _REGEX_KEYWORDS):
            end = _regex_end(source, i)
            if end:
                i, last, word = end, "/", ""
                continue
        if c in "([{":
            stack.append((c, line))
        elif c in ")]}":
            if c == "}" and stack and stack[-1][0] == "${":     # end of ${...} inside a template
                stack.pop()
                i += 1
                i, line, problem = _template(source, i, line, stack)
                if problem:
                    return problem
                last, word = "`", ""
                continue
            if not stack or stack[-1][0] != pairs[c]:
                return f"unexpected '{c}' on line {line}."
            stack.pop()
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (source[j].isalnum() or source[j] in "_$"):
                j += 1
            word, last, i = source[i:j], source[j - 1], j
            continue
        last, word = c, ""
        i += 1
    if stack:
        opener, at = stack[-1]
        what = "template string" if opener == "`" else f"'{opener}'"
        return f"{what} opened on line {at} is never closed (the code is cut off or a bracket is missing)."
    return ""


def _template(source: str, i: int, line: int, stack: list):
    """Scan template-literal text from i until its closing backtick or the next ${ ."""
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            line += 1
        elif c == "`":
            stack.pop()                      # the "`" pushed when the template opened
            return i + 1, line, ""
        elif c == "$" and source[i + 1:i + 2] == "{":
            stack.append(("${", line))
            return i + 2, line, ""
        i += 1
    return i, line, f"a template string opened on line {stack[-1][1]} is never closed."


def _regex_end(source: str, i: int) -> int:
    """Index after a regular-expression literal starting at i, or 0 if this isn't one."""
    j, in_class, n = i + 1, False, len(source)
    while j < n:
        c = source[j]
        if c == "\n":
            return 0
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and source[j].isalpha():      # flags
                j += 1
            return j
        j += 1
    return 0
