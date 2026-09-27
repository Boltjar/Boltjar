"""Tool calling end to end: a wired Tool node, an xAI LLM that invokes it.

Network is fully mocked (httpx.AsyncClient is patched), so no real model is hit.
We assert that the Tool node is registered with the right ports, and that the
LLM's xAI loop dispatches the tool_call to the Tool node (via ctx.call_tool),
appends both the assistant tool_calls message and the role:tool result back into
the chat, and returns the final assistant text.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import boltjar.nodes.core  # noqa: F401  (registers the core nodes)
from boltjar.runtime import Runtime
from boltjar.sdk import NODE_REGISTRY, types


def _async_client_mock(post_impl):
    """A patched httpx.AsyncClient whose .post() runs post_impl(url, **kwargs)."""
    mock_client = AsyncMock()
    mock_client.post = post_impl
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_tool_node_is_registered_with_the_right_ports():
    assert "core.ai.tool" in NODE_REGISTRY
    spec = NODE_REGISTRY["core.ai.tool"]
    # one trigger input (result), two outputs (call as tool-call, done as event).
    in_names = [(p.name, p.type, p.trigger) for p in spec.inputs]
    out_names = [(p.name, p.type) for p in spec.outputs]
    assert in_names == [("result", "text", True)]
    assert out_names == [("call", "tool-call"), ("trigger", "event")]
    # the editor knobs: name / description / schema_mode (auto|manual) / schema.
    # In auto mode the schema is generated from a wired Tool Args node; the raw
    # `schema` knob is the manual override (op-shaped, shown only in manual mode).
    assert {w.name for w in spec.widgets} == {"name", "description", "schema_mode", "schema"}


def test_tool_call_output_is_wireable_into_the_llm_tools_port():
    # Regression: the editor refused to connect a Tool into an LLM because a Tool's
    # `call` output is `tool-call` while the LLM's growable `tools` input is `tool`,
    # and the two were unrelated types (typesCompatible -> false). The wire the
    # runtime + these tests deliberately build (Tool.call -> LLM.tools, see
    # test_xai_tool_loop_dispatches_through_runtime) must type-check: `tool-call`
    # is a SUBTYPE of `tool`, so a tool-call output satisfies a tool input.
    tool = NODE_REGISTRY["core.ai.tool"]
    llm = NODE_REGISTRY["core.ai.llm"]
    call_port = next(p for p in tool.outputs if p.name == "call")
    tools_port = next(p for p in llm.inputs if p.name == "tools")
    assert call_port.type == "tool-call"
    assert tools_port.type == "tool" and tools_port.growable
    # the exact Tool.call -> LLM.tools direction the editor validates on drop.
    assert types.compatible(call_port.type, tools_port.type) is True
    # directional: a bare `tool` must NOT satisfy a `tool-call` input (e.g. Tool Args).
    assert types.compatible("tool", "tool-call") is False


# ---------------------------------------------------------------------------
# Tool loop through the runtime
# ---------------------------------------------------------------------------

def test_xai_tool_loop_dispatches_through_runtime():
    # Two-graph topology:
    #   Manual trigger -> LLM.trigger (fires the LLM)
    #   Tool.call (tool-call) is observed; we drive Tool.result from the test
    #   to simulate a downstream node computing a weather lookup.
    #   Tool wired into LLM.tools so the runtime collects it for the LLM.
    graph = {
        "nodes": [
            {"id": "trig", "type": "core.trigger.manual"},
            {"id": "tool", "type": "core.ai.tool", "config": {
                "name": "get_weather",
                "description": "Get the current weather for a city.",
                "schema": json.dumps({
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                }),
            }},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": "xai/grok-4.3"}},
        ],
        "edges": [
            {"src": "trig", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "tool", "src_port": "call", "dst": "llm", "dst_port": "tools"},
        ],
    }

    # Capture every outgoing xAI body so we can assert the second hop carries
    # both the assistant tool_calls turn AND the role:tool result.
    sent_bodies: list[dict] = []
    responses = [
        # first call: model decides to invoke the tool.
        {"choices": [{"message": {
            "content": "",
            "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
            }],
        }}]},
        # second call (after we feed the tool result back): final answer.
        {"choices": [{"message": {"content": "It's 22C in Paris", "reasoning_content": ""}}]},
    ]

    async def fake_post(url, **kwargs):
        sent_bodies.append(kwargs.get("json"))
        idx = min(len(sent_bodies) - 1, len(responses) - 1)
        fake_resp = MagicMock()
        fake_resp.json.return_value = responses[idx]
        fake_resp.raise_for_status.return_value = None
        return fake_resp

    async def driver():
        rt = Runtime()
        rt.build(graph)
        await rt.run()
        # the tool node has no real downstream; we play that role by watching
        # for the call event landing on the tool, then emitting `result` back.
        tool_inst = rt.nodes["tool"]
        called_args = {}

        async def respond_to_tool_call():
            for _ in range(200):
                await asyncio.sleep(0.01)
                payload = tool_inst.out_latch.get("call")
                if payload is not None:
                    called_args["args"] = payload
                    # An upstream "downstream-of-call" node would normally emit
                    # to the tool's `result` input port; the runtime would then
                    # enqueue on the tool's mailbox. Simulate that delivery.
                    tool_inst.mailbox.put_nowait(("result", "sunny, 22C", rt.new_turn()))
                    return
            raise AssertionError("tool 'call' event never fired")

        # let the manual trigger emit + the LLM start its run, then race the
        # tool responder against the LLM's await on call_tool().
        with patch("httpx.AsyncClient",
                   return_value=_async_client_mock(fake_post)), \
             patch.dict("os.environ", {"XAI_API_KEY": "test-key"}, clear=False):
            responder = asyncio.create_task(respond_to_tool_call())
            for _ in range(300):
                await asyncio.sleep(0.01)
                if rt.nodes["llm"].out_latch.get("response"):
                    break
            await responder
            llm_resp = rt.nodes["llm"].out_latch.get("response")
            await rt.stop()
            return llm_resp, sent_bodies, called_args

    llm_resp, bodies, called_args = asyncio.run(driver())

    # The final assistant text came back through the LLM's `response` output.
    assert llm_resp == "It's 22C in Paris"
    # The Tool node received the parsed arguments (the runtime emits the dict
    # straight through; call_tool emits the args as the event payload).
    assert called_args["args"] == {"city": "Paris"}

    # First hop body declared the tools list correctly.
    assert len(bodies) == 2
    first = bodies[0]
    assert first["model"] == "grok-4.3"
    assert first["tools"] == [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }]

    # Second hop body carries BOTH the assistant tool_calls turn AND the
    # role:tool message keyed by the same tool_call_id.
    second_msgs = bodies[1]["messages"]
    # assistant turn with tool_calls is present.
    asst = next(m for m in second_msgs if m["role"] == "assistant")
    assert asst["tool_calls"][0]["id"] == "c1"
    assert asst["tool_calls"][0]["function"]["name"] == "get_weather"
    # role:tool result is present with the matching id and our payload.
    tool_msg = next(m for m in second_msgs if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["content"] == "sunny, 22C"


# ---------------------------------------------------------------------------
# Anthropic + Ollama tool loops (mirror the xAI test, different wire shapes)
# ---------------------------------------------------------------------------

def _tool_loop_graph(model_id: str) -> dict:
    """Shared graph: Manual -> LLM(<model>) + Tool wired into LLM.tools."""
    return {
        "nodes": [
            {"id": "trig", "type": "core.trigger.manual"},
            {"id": "tool", "type": "core.ai.tool", "config": {
                "name": "get_weather",
                "description": "Get the current weather for a city.",
                "schema": json.dumps({
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                }),
            }},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": model_id}},
        ],
        "edges": [
            {"src": "trig", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "tool", "src_port": "call", "dst": "llm", "dst_port": "tools"},
        ],
    }


async def _drive_tool_loop(graph, fake_post, env):
    """Boot the runtime, race a Tool responder against the LLM, return (resp, bodies, args)."""
    rt = Runtime()
    rt.build(graph)
    await rt.run()
    tool_inst = rt.nodes["tool"]
    called_args = {}

    async def respond_to_tool_call():
        for _ in range(200):
            await asyncio.sleep(0.01)
            payload = tool_inst.out_latch.get("call")
            if payload is not None:
                called_args["args"] = payload
                tool_inst.mailbox.put_nowait(("result", "sunny, 22C", rt.new_turn()))
                return
        raise AssertionError("tool 'call' event never fired")

    with patch("httpx.AsyncClient",
               return_value=_async_client_mock(fake_post)), \
         patch.dict("os.environ", env, clear=False):
        responder = asyncio.create_task(respond_to_tool_call())
        for _ in range(300):
            await asyncio.sleep(0.01)
            if rt.nodes["llm"].out_latch.get("response"):
                break
        await responder
        llm_resp = rt.nodes["llm"].out_latch.get("response")
        await rt.stop()
        return llm_resp, called_args


def test_anthropic_tool_loop_dispatches_through_runtime():
    graph = _tool_loop_graph("anthropic/claude-sonnet-4-6")
    sent_bodies: list[dict] = []
    responses = [
        # first call: assistant content carries a tool_use block (input is an
        # already-parsed object, not a JSON string like xAI's `arguments`).
        {"role": "assistant",
         "content": [{"type": "tool_use", "id": "tu1",
                      "name": "get_weather", "input": {"city": "Paris"}}],
         "stop_reason": "tool_use"},
        # second call (after the tool result is fed back): plain text answer.
        {"content": [{"type": "text", "text": "22C in Paris"}]},
    ]

    async def fake_post(url, **kwargs):
        sent_bodies.append(kwargs.get("json"))
        idx = min(len(sent_bodies) - 1, len(responses) - 1)
        fake_resp = MagicMock()
        fake_resp.json.return_value = responses[idx]
        fake_resp.raise_for_status.return_value = None
        return fake_resp

    llm_resp, called_args = asyncio.run(_drive_tool_loop(
        graph, fake_post, {"ANTHROPIC_API_KEY": "test-key"}))

    assert llm_resp == "22C in Paris"
    assert called_args["args"] == {"city": "Paris"}

    # First hop body declared the tools list correctly (Anthropic shape:
    # bare {name, description, input_schema}, NOT wrapped in {type:function}).
    assert len(sent_bodies) == 2
    first = sent_bodies[0]
    assert first["model"] == "claude-sonnet-4-6"
    assert first["tools"] == [{
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }]

    # Second hop body: the assistant turn carries the tool_use block verbatim,
    # then the user turn's content is a tool_result block keyed by tool_use_id.
    second_msgs = sent_bodies[1]["messages"]
    asst = next(m for m in second_msgs if m["role"] == "assistant")
    asst_blocks = asst["content"]
    assert isinstance(asst_blocks, list)
    assert any(b.get("type") == "tool_use" and b.get("id") == "tu1"
               and b.get("name") == "get_weather"
               for b in asst_blocks)
    # the LAST user message is the tool_result follow-up (the first user
    # message is the original prompt).
    tool_user = [m for m in second_msgs if m["role"] == "user"][-1]
    assert tool_user["content"] == [{
        "type": "tool_result", "tool_use_id": "tu1", "content": "sunny, 22C"}]


def test_ollama_tool_loop_dispatches_through_runtime():
    graph = _tool_loop_graph("ollama/gemma4:e4b")
    sent_bodies: list[dict] = []
    sent_urls: list[str] = []
    responses = [
        # first call: assistant message with tool_calls; arguments is an
        # already-parsed object (NOT a JSON string like xAI's).
        {"message": {"role": "assistant",
                     "tool_calls": [{
                         "function": {"name": "get_weather",
                                      "arguments": {"city": "Paris"}}}]}},
        # second call (after we feed the role:tool result back): plain text.
        {"message": {"content": "22C in Paris"}},
    ]

    async def fake_post(url, **kwargs):
        sent_urls.append(url)
        sent_bodies.append(kwargs.get("json"))
        idx = min(len(sent_bodies) - 1, len(responses) - 1)
        fake_resp = MagicMock()
        fake_resp.json.return_value = responses[idx]
        fake_resp.raise_for_status.return_value = None
        return fake_resp

    llm_resp, called_args = asyncio.run(_drive_tool_loop(
        graph, fake_post, {}))

    assert llm_resp == "22C in Paris"
    assert called_args["args"] == {"city": "Paris"}

    # Tool path uses /api/chat (not /api/generate).
    assert all(u.endswith("/api/chat") for u in sent_urls)
    assert len(sent_bodies) == 2
    first = sent_bodies[0]
    assert first["model"] == "gemma4:e4b"
    # Ollama tool shape mirrors OpenAI's {type, function}.
    assert first["tools"] == [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }]

    # Second hop: the assistant turn is appended verbatim, then a role:tool
    # message with our result (Ollama matches by order/role, no tool_call_id).
    second_msgs = sent_bodies[1]["messages"]
    asst = next(m for m in second_msgs if m["role"] == "assistant")
    assert asst["tool_calls"][0]["function"]["name"] == "get_weather"
    tool_msg = next(m for m in second_msgs if m["role"] == "tool")
    assert tool_msg == {"role": "tool", "content": "sunny, 22C"}
    assert "tool_call_id" not in tool_msg


# ---------------------------------------------------------------------------
# Tool Args splitter + schema-from-fields (the intuitiveness rework)
# ---------------------------------------------------------------------------

def test_tool_args_splits_a_call_into_per_field_outputs():
    assert "core.ai.tool_args" in NODE_REGISTRY
    spec = NODE_REGISTRY["core.ai.tool_args"]
    # `call` is a TRIGGER (fires the body); `trigger` output drives the rest.
    assert [(p.name, p.type, p.trigger) for p in spec.inputs] == [("call", "tool-call", True)]
    assert [(p.name, p.type) for p in spec.outputs] == [("trigger", "event")]
    obj = spec.cls()
    obj._node_cfg = {"fields": "location, unit"}
    # each declared field reads its arg (+ a trigger to drive on); extra keys drop.
    assert obj.run(call={"location": "Lisbon", "unit": "C", "extra": 9}) == {
        "trigger": True, "location": "Lisbon", "unit": "C"}
    # a non-dict (or missing field) resolves every field to None, still triggers.
    obj2 = spec.cls()
    obj2._node_cfg = {"fields": "a b"}
    assert obj2.run(call=None) == {"trigger": True, "a": None, "b": None}


def test_llm_derives_tool_schema_from_wired_tool_args():
    """In auto mode (default) the tool's JSON schema is GENERATED from the field
    names of a wired Tool Args node; manual mode uses the raw schema knob."""
    from boltjar.nodes.core.builtin import _tool_schema

    graph = {
        "nodes": [
            {"id": "tool", "type": "core.ai.tool", "config": {"name": "get_weather"}},
            {"id": "args", "type": "core.ai.tool_args", "config": {"fields": "city, unit"}},
        ],
        "edges": [
            {"src": "tool", "src_port": "call", "dst": "args", "dst_port": "call"},
        ],
    }
    rt = Runtime()
    rt.build(graph)
    tobj = rt.nodes["tool"].obj
    # auto: schema synthesized from the Tool Args fields (declare args once).
    assert _tool_schema(rt, "tool", tobj) == {
        "type": "object",
        "properties": {"city": {"type": "string"}, "unit": {"type": "string"}},
        "required": ["city", "unit"],
    }
    # manual: the raw schema knob wins.
    tobj.schema_mode = "manual"
    tobj.schema = '{"type":"object","properties":{"x":{"type":"number"}},"required":["x"]}'
    assert _tool_schema(rt, "tool", tobj)["properties"] == {"x": {"type": "number"}}


def test_tool_body_fires_through_tool_args_end_to_end():
    """The Tool Args node IS the tool body's entry: wired Tool.call -> ToolArgs ->
    result, it FIRES on a real call (no manual result emit) and the answer flows
    back to the model. Also proves the schema was generated from its fields."""
    graph = {
        "nodes": [
            {"id": "trig", "type": "core.trigger.manual"},
            {"id": "tool", "type": "core.ai.tool", "config": {"name": "get_weather"}},
            {"id": "args", "type": "core.ai.tool_args", "config": {"fields": "city"}},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": "xai/grok-4.3"}},
        ],
        "edges": [
            {"src": "trig", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "tool", "src_port": "call", "dst": "llm", "dst_port": "tools"},
            # the tool body: call fires Tool Args, which feeds the arg straight back
            # as the result (a degenerate body that proves the firing + round-trip).
            {"src": "tool", "src_port": "call", "dst": "args", "dst_port": "call"},
            {"src": "args", "src_port": "city", "dst": "tool", "dst_port": "result"},
        ],
    }
    sent_bodies: list[dict] = []
    responses = [
        {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]}}]},
        {"choices": [{"message": {"content": "It's sunny in Paris", "reasoning_content": ""}}]},
    ]

    async def fake_post(url, **kwargs):
        sent_bodies.append(kwargs.get("json"))
        idx = min(len(sent_bodies) - 1, len(responses) - 1)
        r = MagicMock()
        r.json.return_value = responses[idx]
        r.raise_for_status.return_value = None
        return r

    async def driver():
        rt = Runtime()
        rt.build(graph)
        await rt.run()
        with patch("httpx.AsyncClient", return_value=_async_client_mock(fake_post)), \
             patch.dict("os.environ", {"XAI_API_KEY": "test-key"}, clear=False):
            for _ in range(400):
                await asyncio.sleep(0.01)
                if rt.nodes["llm"].out_latch.get("response"):
                    break
            resp = rt.nodes["llm"].out_latch.get("response")
            await rt.stop()
            return resp, sent_bodies

    resp, bodies = asyncio.run(driver())
    # the model's final answer came back: the loop closed through the real body.
    assert resp == "It's sunny in Paris"
    # the schema the model saw was GENERATED from the Tool Args `fields` (city).
    assert bodies[0]["tools"][0]["function"]["parameters"] == {
        "type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
    # the body fed the result back (Tool Args fired and routed the arg to result):
    # the second hop carries the role:tool message with our value.
    tool_msg = next(m for m in bodies[1]["messages"] if m["role"] == "tool")
    assert tool_msg["content"] == "Paris"


def test_tools_on_editor_named_sockets_reach_the_model(vendor_http):
    """The editor names the LLM's tool sockets tool0, tool1 and so on; a
    hand-written graph may use `tools` or `tools_1`. A Tool's `call` on any of
    them is offered to the model, in wiring order. A Tool wired into the LLM by
    its `trigger` is not a tool."""
    import httpx

    graph = {
        "nodes": [
            {"id": "go", "type": "core.trigger.manual"},
            {"id": "clock", "type": "core.ai.tool",
             "config": {"name": "get_time", "description": "The time."}},
            {"id": "weather", "type": "core.ai.tool",
             "config": {"name": "get_weather", "description": "The weather."}},
            {"id": "news", "type": "core.ai.tool",
             "config": {"name": "get_news", "description": "The news."}},
            {"id": "search", "type": "core.ai.tool",
             "config": {"name": "search", "description": "A search."}},
            {"id": "decoy", "type": "core.ai.tool",
             "config": {"name": "not_a_tool", "description": "Wired by its trigger."}},
            {"id": "llm", "type": "core.ai.llm", "config": {"model": "ollama/qwen3:14b"}},
        ],
        "edges": [
            {"src": "go", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
            {"src": "clock", "src_port": "call", "dst": "llm", "dst_port": "tool0"},
            {"src": "weather", "src_port": "call", "dst": "llm", "dst_port": "tool1"},
            {"src": "news", "src_port": "call", "dst": "llm", "dst_port": "tools"},
            {"src": "search", "src_port": "call", "dst": "llm", "dst_port": "tools_1"},
            {"src": "decoy", "src_port": "trigger", "dst": "llm", "dst_port": "trigger"},
        ],
    }
    vendor_http.reply = lambda request: httpx.Response(
        200, json={"message": {"role": "assistant", "content": "done"}})

    async def driver():
        rt = Runtime()
        rt.build(graph)
        await rt.run()  # the Manual fires the LLM at once
        for _ in range(300):
            await asyncio.sleep(0.01)
            if rt.nodes["llm"].out_latch.get("response"):
                break
        resp = rt.nodes["llm"].out_latch.get("response")
        await rt.stop()
        return resp

    assert asyncio.run(driver()) == "done"
    assert str(vendor_http.last.url).endswith("/api/chat")
    offered = [t["function"]["name"] for t in vendor_http.last_json()["tools"]]
    assert offered == ["get_time", "get_weather", "get_news", "search"]


def test_a_growable_socket_is_known_by_what_its_wire_carries():
    """growable_sources reads a socket of a growable input from its wire, not its
    name: declared ports, promoted knobs and wires of another type are left out."""
    graph = {
        "nodes": [
            {"id": "t", "type": "core.ai.tool", "config": {"name": "a"}},
            {"id": "u", "type": "core.ai.tool", "config": {"name": "b"}},
            {"id": "temp", "type": "core.value.float", "config": {"number": 0.2}},
            {"id": "text", "type": "core.value.text", "config": {"text": "hi"}},
            {"id": "llm", "type": "core.ai.llm",
             "config": {"model": "ollama/qwen3:14b", "promoted": ["temperature"]}},
        ],
        "edges": [
            {"src": "t", "src_port": "call", "dst": "llm", "dst_port": "anything"},
            {"src": "u", "src_port": "call", "dst": "llm", "dst_port": "tool7"},
            {"src": "temp", "src_port": "out", "dst": "llm", "dst_port": "temperature"},
            {"src": "text", "src_port": "out", "dst": "llm", "dst_port": "prompt"},
        ],
    }
    rt = Runtime()
    rt.build(graph)
    assert rt.growable_sources("llm", "tools") == [("t", "call"), ("u", "call")]
    assert rt.growable_sources("llm", "prompt") == []  # not a growable input
    assert rt.growable_sources("nope", "tools") == []
