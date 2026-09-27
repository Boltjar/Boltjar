# SPDX-License-Identifier: MIT-0
"""The Hello pack's nodes. Knobs are explicit Widgets, so the editor draws each
one exactly as declared."""
from boltjar.sdk import Kind, Port, Widget, node


@node(id="hello.shout", name="Shout", kind=Kind.TRANSFORM, category="Hello",
      pulled=True, summary="Upper-case the text and add a suffix.")
class Shout:
    """A pulled (data) node: evaluated when a node downstream needs its output."""
    suffix = Widget(kind="text", default="!")
    inputs = [Port("text", "text")]
    outputs = [Port("out", "text")]

    def run(self, text=None, **_):  # a pulled node's run is synchronous
        return {"out": str(text or "").upper() + str(self.suffix or "")}


@node(id="hello.greet", name="Greet", kind=Kind.TRANSFORM, category="Hello",
      summary="Greet a name each time a trigger arrives.")
class Greet:
    """A fired node: runs when its trigger input fires, pulls `name`, and passes
    the trigger on so the next node can fire."""
    greeting = Widget(kind="text", default="Hello")
    times = Widget(kind="number", default=1, min=1, max=5, step=1)
    inputs = [Port("trigger", "event", trigger=True), Port("name", "text", optional=True)]
    outputs = [Port("out", "text"), Port("trigger", "event")]

    def run(self, trigger=None, name=None, **_):
        line = f"{self.greeting}, {name or 'world'}!"
        return {"out": " ".join([line] * int(self.times)), "trigger": True}
