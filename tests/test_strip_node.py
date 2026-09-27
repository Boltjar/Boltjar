"""The Strip text cleaner (core.text.strip): a composable TTS-and-beyond cleaner.

Each concern (emoji, markdown, [tags], *actions*, urls) is an independent toggle,
all default ON. These tests exercise each toggle alone, a couple of combinations,
and the whitespace collapse, plus the headline gap it closes: TTS no longer speaks
the emoji.
"""
from boltjar.sdk import NODE_REGISTRY
import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
import boltjar.nodes.core.builtin as builtin


def _strip(text, **toggles):
    """Run the node with an explicit config (defaults ON, override per test)."""
    cfg = {"emoji": True, "markdown": True, "tags": True, "actions": True, "urls": True}
    cfg.update(toggles)
    obj = NODE_REGISTRY["core.text.strip"].cls()
    obj._node_cfg = cfg
    obj._node_id = "n"
    return obj.run(**{"in": text})["out"]


# --------------------------------------------------------------- each toggle alone

def test_emoji_only():
    assert _strip("hello 😂 world 🥺", emoji=True, markdown=False, tags=False,
                  actions=False, urls=False) == "hello world"
    # a compound emoji (face + ZWJ + puff) is removed whole, no orphan joiner.
    assert _strip("ok 😮‍💨 done", emoji=True, markdown=False, tags=False,
                  actions=False, urls=False) == "ok done"
    # emoji OFF leaves them in place.
    assert _strip("hi 😂", emoji=False, markdown=False, tags=False,
                  actions=False, urls=False) == "hi 😂"


def test_markdown_only():
    out = _strip("**bold** and *italic* and `code`", emoji=False, markdown=True,
                 tags=False, actions=False, urls=False)
    assert out == "bold and italic and code"
    # code fence keeps the inner text, drops the fence + language.
    assert _strip("```python\nx = 1\n```", emoji=False, markdown=True, tags=False,
                  actions=False, urls=False) == "x = 1"
    # heading / blockquote / bullet markers go; the text stays.
    assert _strip("# Title", emoji=False, markdown=True, tags=False,
                  actions=False, urls=False) == "Title"
    assert _strip("- item", emoji=False, markdown=True, tags=False,
                  actions=False, urls=False) == "item"
    # a markdown link keeps its text, drops the target.
    assert _strip("see [docs](http://x.com)", emoji=False, markdown=True, tags=False,
                  actions=False, urls=False) == "see docs"


def test_tags_only_reuses_tagparse_regex():
    # the [bracket] tag syntax Tag Parse consumes is stripped (same regex).
    assert _strip("Hello [laughing] there", emoji=False, markdown=False, tags=True,
                  actions=False, urls=False) == "Hello there"
    assert builtin._TAG_RE.search("[laughing]") is not None  # one shared owner
    # tags OFF leaves the bracket text.
    assert _strip("Hi [wave]", emoji=False, markdown=False, tags=False,
                  actions=False, urls=False) == "Hi [wave]"


def test_actions_only_removes_the_whole_span():
    # a *stage direction* is removed entirely (text and all), unlike italic.
    assert _strip("Well *sighs* fine", emoji=False, markdown=False, tags=False,
                  actions=True, urls=False) == "Well fine"
    # double-asterisk bold is NOT touched by the actions pass (left for markdown).
    assert _strip("**keep**", emoji=False, markdown=False, tags=False,
                  actions=True, urls=False) == "**keep**"


def test_urls_only():
    assert _strip("go to https://example.com/x now", emoji=False, markdown=False,
                  tags=False, actions=False, urls=True) == "go to now"
    assert _strip("at www.example.com", emoji=False, markdown=False, tags=False,
                  actions=False, urls=True) == "at"
    assert _strip("keep https://x.com", emoji=False, markdown=False, tags=False,
                  actions=False, urls=False) == "keep https://x.com"


# --------------------------------------------------------------- combinations

def test_actions_vs_markdown_italic_distinction():
    # both ON: a single-* span is dropped as an ACTION (whole), while a double-*
    # bold survives as its text: the two asterisk syntaxes disambiguated.
    assert _strip("**bold** *sighs* end") == "bold end"
    # actions OFF but markdown ON: a single-* span is italic -> keep its text.
    assert _strip("a *word* b", actions=False) == "a word b"


def test_everything_on_tts_ready():
    raw = "Hi [excited] **friend** 😂 *waves* check https://x.com `now`"
    # emoji gone, bold/inline-code text kept, tag + action + url gone, space tidy.
    assert _strip(raw) == "Hi friend check now"


def test_whitespace_collapses_after_removals():
    # gaps left by removed markup close up to single spaces, trimmed at the ends.
    assert _strip("  a   😂   b  ") == "a b"
    # _TAG_RE matches letters/spaces only, so both bracket tags here are stripped.
    assert _strip("x [tag]   [mood] y") == "x y"


def test_empty_and_none_are_safe():
    assert _strip("") == ""
    # a None input coerces to empty (the node never raises on a missing wire).
    obj = NODE_REGISTRY["core.text.strip"].cls()
    obj._node_cfg = {}
    obj._node_id = "n"
    assert obj.run()["out"] == ""


def test_all_toggles_off_is_identity_but_for_whitespace():
    raw = "keep 😂 **all** [tags] *actions* https://x.com"
    out = _strip(raw, emoji=False, markdown=False, tags=False, actions=False, urls=False)
    assert out == raw  # nothing stripped; single spaces already, so unchanged
