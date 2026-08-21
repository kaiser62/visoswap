"""Every ``torch.load`` under ``visoswap/`` must refuse to execute pickled code.

``torch.load`` unpickles by default, and unpickling is arbitrary code execution
by design: a crafted weights file runs whatever it likes at load time, with the
privileges of the process that opened it. ``weights_only=True`` switches torch
to a restricted unpickler that can materialise tensors and plain containers and
nothing else. That keyword is the whole mitigation, so this file exists to make
sure no call in the vendored tree is missing it.

**Why a syntax tree rather than a grep.** ``clipseg.py`` has three ``torch.load``
calls stacked on consecutive lines, two of them commented out by upstream, and
the live one is the middle one. A line pattern would either match the comments
or need to filter them, and it would still miss a call wrapped across lines. The
grammar answers the question directly: a commented-out call is not a call, so it
is a non-hit *by construction* rather than by filtering. That is the same
argument plan 02-01 made for using ``ast`` in the backend-import scan.

**Scope, stated rather than assumed.** This gate covers ``torch.load`` and only
``torch.load``, because ``weights_only`` is a ``torch.load`` keyword and no other
deserialiser in this tree has an equivalent. The two neighbours that might look
like omissions are covered by their own tests below so that nobody has to guess
whether they were considered:

* ``torch.jit.load`` parses a TorchScript archive and takes no ``weights_only``.
  Demanding the keyword there would make the gate impossible to pass, so the
  scanner resolves the *full* dotted chain and ``torch.jit.load`` is deliberately
  not a match. :func:`test_the_scanner_does_not_mistake_torch_jit_load_for_torch_load`
  pins that, because getting it wrong in the other direction -- matching on a
  trailing ``.load`` -- would also match ``json.load`` and ``clip.load``.
* ``pickle.load`` has no safe mode at all. The three call sites in this tree are
  inventoried by :func:`test_the_pickle_load_inventory_has_not_grown` so a new
  one cannot appear unnoticed; they are threat T-02-13, accepted rather than
  mitigated, and Phase 4's hash-verified model bootstrap is where their integrity
  becomes checkable.

**The gate is shown capable of failing** rather than assumed to be. Plans 01-01
and 01-02 both had to add exactly this proof after the fact, for exactly this
reason: a gate that has never been watched fail is a gate whose state nobody
knows.
"""

import ast
from pathlib import Path
from typing import NamedTuple, Optional

from tests.conftest import REPO_ROOT, vendored_sources

#: The call this gate governs, by its full dotted name. Matching the *chain*
#: rather than the trailing attribute is load-bearing in both directions --
#: see the module docstring.
HARDENED_CALL = "torch.load"

#: The keyword that switches torch to the restricted unpickler.
SAFE_KEYWORD = "weights_only"

#: The deserialiser with no safe mode, recorded rather than gated. Repo-relative
#: POSIX path -> line number, as measured. Threat T-02-13.
#:
#: This is an inventory, not an allowlist: nothing here is exempted from
#: anything, because there is no keyword to exempt it from. Its only job is to
#: make a *fourth* ``pickle.load`` appearing in the tree impossible to miss.
KNOWN_PICKLE_LOAD_SITES = {
    # A precomputed prompt-vector cache, guarded by an ``isfile`` on a
    # CWD-relative name that does not exist in this repository.
    ("visoswap/processors/external/clipseg.py", 118),
    # ``liveportrait_onnx/lip_array.pkl``, 658 bytes, from the same model set the
    # project already trusts wholesale.
    ("visoswap/processors/face_editors.py", 35),
    # ``meanshape_68.pkl``, same model set, same disposition.
    ("visoswap/processors/face_landmark_detectors.py", 63),
}


class LoadSite(NamedTuple):
    """One ``torch.load`` call and the state of its safety keyword."""

    label: str
    lineno: int
    keyword: Optional[str]

    @property
    def is_hardened(self) -> bool:
        return self.keyword == "True"

    def describe(self) -> str:
        return "{}:{}: torch.load({}={})".format(
            self.label, self.lineno, SAFE_KEYWORD, self.keyword or "<absent>"
        )


def dotted_name(node: ast.AST) -> Optional[str]:
    """``torch.jit.load`` for the attribute chain, ``None`` for anything else.

    Returns ``None`` rather than a partial name for a call on an expression --
    ``get_module().load(...)`` -- because a partial name would be a guess, and a
    guess is how a scanner starts reporting things that are not there.
    """
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def scan_source(text: str, label: str) -> list[LoadSite]:
    """Every ``torch.load`` in ``text``, with its ``weights_only`` argument.

    ``keyword`` is the *unparsed source* of the argument's value, or ``None``
    when the keyword is absent. Reporting the source rather than a boolean is
    what lets the failure message say ``weights_only=flag`` -- a call that passes
    a variable looks hardened to a reader and is not hardened at all, since the
    variable can be false.

    A ``**kwargs`` splat is reported as ``**`` and therefore as unhardened: the
    keyword may well be in there, but "may well be" is not a proof, and a gate
    that accepts a splat is a gate with a one-line bypass.
    """
    sites: list[LoadSite] = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        if dotted_name(node.func) != HARDENED_CALL:
            continue
        keyword: Optional[str] = None
        for kw in node.keywords:
            if kw.arg is None:
                keyword = "**"
                continue
            if kw.arg == SAFE_KEYWORD:
                keyword = (
                    "True"
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True
                    else ast.unparse(kw.value)
                )
                break
        sites.append(LoadSite(label=label, lineno=node.lineno, keyword=keyword))
    return sites


def scan_tree() -> list[LoadSite]:
    """Every ``torch.load`` under ``visoswap/``.

    ``vendored_sources()`` is reused rather than re-walked so this gate covers
    exactly the tree the Qt import gate and the static source scan cover. Three
    gates walking the tree three ways is three chances for one of them to drift
    off a newly vendored file.
    """
    sites: list[LoadSite] = []
    for path in vendored_sources():
        label = Path(path).resolve().relative_to(REPO_ROOT).as_posix()
        sites.extend(
            scan_source(path.read_text(encoding="utf-8", errors="strict"), label)
        )
    return sites


def test_the_scanned_tree_is_not_empty():
    """A gate over zero files reports the same green as a gate over a clean tree."""
    assert vendored_sources(), (
        "no .py files found under {}/visoswap -- every assertion below would "
        "pass vacuously. If the tree really is empty, this is the test that "
        "should say so.".format(REPO_ROOT)
    )


def test_the_scan_finds_more_than_one_torch_load():
    """Non-vacuity, sharpened: this tree is *known* to hold several call sites.

    A mis-rooted scan, a broken ``vendored_sources()`` or a parser that silently
    swallowed every file all produce zero hits and a green run. Requiring more
    than one hit turns all three into a failure. Two is the floor the plan set;
    the count is printed on failure because the number moving is itself news.
    """
    sites = scan_tree()
    assert len(sites) >= 2, (
        "expected at least two torch.load call sites under visoswap/, found "
        "{}: {}. Fewer than two means the scan is looking at the wrong tree, "
        "not that the tree got safer.".format(
            len(sites), [site.describe() for site in sites]
        )
    )


def test_every_torch_load_refuses_to_execute_pickled_code():
    """The gate itself. Every call passes ``weights_only`` as a literal true."""
    unhardened = [site for site in scan_tree() if not site.is_hardened]
    assert not unhardened, (
        "torch.load call sites that can execute arbitrary code from a weights "
        "file:\n"
        + "\n".join("  " + site.describe() for site in unhardened)
        + "\n\nAdd {}=True. A variable is not enough -- it can be false, and a "
        "reader cannot tell from the call site.".format(SAFE_KEYWORD)
    )


def test_the_scanner_is_capable_of_failing():
    """Strip the keyword from a real vendored file; the scanner must notice.

    The file's own bytes are used, not a hand-written sample, so this proves the
    scanner against the real thing. The mutation is applied to a **copy in
    memory** rather than to the tracked file: a test that edits the working tree
    and restores it afterwards leaves the tree broken if it dies in between, and
    the byte-identity-to-upstream constraint on vendored files makes that an
    expensive way to fail. The copy carries the same bytes, so it proves the same
    thing at none of the risk.

    Both halves are asserted -- mutated fails, original passes -- because a
    scanner that reports a violation for *every* input would also pass the first
    half on its own.
    """
    path = REPO_ROOT / "visoswap" / "processors" / "external" / "clipseg.py"
    original = path.read_text(encoding="utf-8")

    assert (
        "{}=True".format(SAFE_KEYWORD) in original
    ), "clipseg.py no longer carries the keyword this proof mutates"

    mutated = original.replace(", {}=True".format(SAFE_KEYWORD), "")
    assert mutated != original, "the mutation changed nothing"

    caught = [
        site
        for site in scan_source(mutated, "clipseg.py<mutated>")
        if not site.is_hardened
    ]
    assert caught, (
        "the scanner reported no violation for a copy of clipseg.py with "
        "{}=True stripped out. The gate cannot fail, so its green says "
        "nothing.".format(SAFE_KEYWORD)
    )

    restored = [
        site
        for site in scan_source(original, "clipseg.py<original>")
        if not site.is_hardened
    ]
    assert not restored, (
        "the unmutated clipseg.py is reported as unhardened: {}".format(
            [site.describe() for site in restored]
        )
    )


def test_the_scanner_does_not_mistake_torch_jit_load_for_torch_load():
    """Matching a trailing ``.load`` would break the gate in both directions.

    Too wide: ``torch.jit.load``, ``json.load`` and ``clip.load`` take no
    ``weights_only``, so the gate would demand a keyword that does not exist and
    could never go green. Too narrow: a bare ``load(...)`` would slip past --
    which is what :func:`test_no_vendored_module_imports_load_out_of_torch`
    covers.
    """
    sample = (
        "import json, pickle, torch\n"
        "a = torch.jit.load(f)\n"
        "b = json.load(f)\n"
        "c = pickle.load(f)\n"
        "d = clip.load('ViT-B/16')\n"
        "e = torch.load(f, weights_only=True)\n"
    )
    sites = scan_source(sample, "<sample>")
    assert [site.lineno for site in sites] == [6], (
        "the scanner matched something other than the single torch.load on line "
        "6: {}".format([site.describe() for site in sites])
    )


def test_the_scanner_rejects_a_keyword_that_is_not_a_literal_true():
    """``weights_only=flag`` reads as hardened and is not. So does ``**kwargs``."""
    sample = (
        "import torch\n"
        "a = torch.load(f, weights_only=flag)\n"
        "b = torch.load(f, weights_only=False)\n"
        "c = torch.load(f, **options)\n"
        "d = torch.load(f)\n"
        "e = torch.load(f, weights_only=True)\n"
    )
    sites = {site.lineno: site for site in scan_source(sample, "<sample>")}
    assert set(sites) == {2, 3, 4, 5, 6}
    assert sites[2].keyword == "flag"
    assert sites[3].keyword == "False"
    assert sites[4].keyword == "**"
    assert sites[5].keyword is None
    assert [line for line, site in sorted(sites.items()) if not site.is_hardened] == [
        2,
        3,
        4,
        5,
    ]


def test_no_vendored_module_imports_load_out_of_torch():
    """``from torch import load`` would make every call site a bare ``load(...)``.

    The scanner resolves attribute chains, so an aliased import is the one shape
    it cannot see. Rather than teach it to track aliases -- which is a symbol
    table, and a symbol table is where a test stops being readable -- forbid the
    alias. Nothing in the tree wants it, and forbidding it keeps the scanner's
    single rule true.
    """
    offenders = []
    for path in vendored_sources():
        label = Path(path).resolve().relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "torch"
            ):
                for alias in node.names:
                    if alias.name == "load":
                        offenders.append("{}:{}".format(label, node.lineno))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "torch" and alias.asname not in (None, "torch"):
                        offenders.append(
                            "{}:{} (import torch as {})".format(
                                label, node.lineno, alias.asname
                            )
                        )
    assert not offenders, (
        "torch.load is reachable under a name the scanner cannot resolve at: "
        "{}. Use `import torch` and call `torch.load(...)`.".format(offenders)
    )


def test_the_pickle_load_inventory_has_not_grown():
    """``pickle.load`` has no safe mode, so it is recorded rather than gated.

    Recording it is not a formality. The gate above can only speak about
    ``torch.load``; without this, a reader could reasonably conclude from a green
    suite that nothing in the tree unpickles unsafely, which is false. Threat
    T-02-13 accepts these three on the grounds that they read the same 12GB model
    set the project already trusts wholesale, and Phase 4's hash-verified
    bootstrap is where that trust becomes checkable. A *fourth* site would be
    outside that argument, so it fails here and gets its own decision.
    """
    found = set()
    for path in vendored_sources():
        label = Path(path).resolve().relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and dotted_name(node.func) in (
                "pickle.load",
                "pickle.loads",
            ):
                found.add((label, node.lineno))

    added = sorted(found - KNOWN_PICKLE_LOAD_SITES)
    removed = sorted(KNOWN_PICKLE_LOAD_SITES - found)
    assert not added, (
        "new pickle.load call site(s) at {}. pickle has no weights_only, so a "
        "new one is a new arbitrary-code-execution surface that needs its own "
        "recorded decision -- add it here with the reason, or remove the "
        "call.".format(added)
    )
    assert not removed, (
        "KNOWN_PICKLE_LOAD_SITES names {} which no longer exist. A stale entry "
        "silently absolves whatever lands on that line next.".format(removed)
    )
