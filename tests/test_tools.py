from jarvis import confirm, desktop, instructions, tools

# The voice tools JARVIS had before the registry: none may get lost.
CURRENT_TOOLS = {"delegate_to_claude", "open_app", "open_url", "cancel_task", "system_control",
                 "get_status", "schedule", "cancel_schedule", "remember", "forget",
                 "look_at_screen", "look_at_camera", "display_card", "display_report",
                 "end_conversation"}


def test_every_tool_is_handled_somewhere():
    names = [t["name"] for t in tools.session_tools()]
    assert len(names) == len(set(names))
    assert set(names) == set(tools.handlers()) | tools.client_tools()
    assert not set(tools.handlers()) & tools.client_tools()
    for fam in tools.FAMILIES:  # each family handles its own tools
        own = {t["name"] for t in fam.TOOLS}
        assert own == set(fam.HANDLERS) | fam.CLIENT_TOOLS, fam.__name__
        assert isinstance(fam.instructions_block(), str)


def test_registry_keeps_the_current_tools():
    assert CURRENT_TOOLS <= {t["name"] for t in tools.session_tools()}  # later packages add more
    assert tools.client_tools() >= {"display_card", "display_report", "look_at_camera",
                                    "end_conversation"}


def test_hidden_family_offers_no_tools(monkeypatch):
    monkeypatch.setattr(tools.tools_memory, "available", lambda: False)
    names = {t["name"] for t in tools.session_tools()}
    assert "remember" not in names and "forget" not in names
    assert tools.run_tool("remember", {"fact": "x"})["ok"] is False


def test_every_call_goes_through_the_confirmation_gate(monkeypatch):
    seen = []

    def gate(name, args, ctx):
        seen.append((name, args, ctx.session_id, ctx.origin))
        return {"status": "needs_confirmation", "pending_id": "p1"}

    monkeypatch.setattr(confirm, "gate", gate)
    out = tools.run_tool("delegate_to_claude", {"title": "x", "prompt": "y"}, tools.ToolCtx("s1"))
    assert out["status"] == "needs_confirmation"
    assert seen == [("delegate_to_claude", {"title": "x", "prompt": "y"}, "s1", "pc")]
    assert not tools.tasks.TASKS  # parked, not started
    # A refusal from the gate comes back as it is.
    monkeypatch.setattr(confirm, "gate", lambda name, args, ctx: {"ok": False, "error": "Non."})
    assert tools.run_tool("get_status", {}, tools.ToolCtx("s1")) == {"ok": False, "error": "Non."}


def test_the_effective_origin_reaches_the_gate_and_the_handler(monkeypatch):
    """A caller saying "pc" in a phone's session is the phone; two remote
    origins never share one."""
    confirm.SESSIONS.clear()
    phone = confirm.new_session(origin="app:d_0123456789abcdef")
    seen = []
    monkeypatch.setattr(confirm, "gate", lambda name, args, ctx: seen.append(("gate", ctx.origin)))
    monkeypatch.setitem(tools.HANDLERS, "get_status", lambda a, ctx: seen.append(("handler", ctx.origin)) or {})
    monkeypatch.setattr(confirm, "after_tool", lambda name, args, ctx, out: seen.append(("after", ctx.origin)))
    tools.run_tool("get_status", {}, tools.ToolCtx(phone))
    assert seen == [("gate", "app:d_0123456789abcdef"), ("handler", "app:d_0123456789abcdef"),
                    ("after", "app:d_0123456789abcdef")]
    assert tools.ToolCtx().origin == "pc"
    out = tools.run_tool("get_status", {}, tools.ToolCtx(phone, origin="app:d_fedcba9876543210"))
    assert out == {"ok": False, "error": "Session d'un autre appareil."}
    seen.clear()
    tools.run_tool("get_status", {}, tools.ToolCtx("unknown-sid", origin="siri:k_9b8a7c6d5e4f3a21"))
    tools.run_tool("get_status", {}, tools.ToolCtx("unknown-sid"))
    # Siri may not ask for the status; an unknown session on the PC stays the PC's.
    assert seen == [("gate", "pc"), ("handler", "pc"), ("after", "pc")]
    confirm.SESSIONS.clear()


def test_remote_allowlists_name_real_tools():
    names = {t["name"] for fam in tools.FAMILIES for t in fam.TOOLS}
    assert tools.APP_TOOLS <= names and tools.SIRI_TOOLS <= tools.APP_TOOLS
    [system] = [t for t in tools.tools_pc.TOOLS if t["name"] == "system_control"]
    actions = set(system["parameters"]["properties"]["action"]["enum"])
    assert tools.REMOTE_PC_ACTIONS < actions
    assert not tools.REMOTE_PC_ACTIONS & {"read_clipboard", "write_clipboard", "save_screenshot"}
    assert set(confirm.ACTION_FR) == tools.REMOTE_PC_ACTIONS  # every one has its wording on the card
    assert {"open_app", "look_at_screen"}.isdisjoint(tools.APP_TOOLS)


def test_app_tools_list_keeps_the_page_tools_and_drops_the_pc_ones():
    app = tools.session_tools("app")
    names = [t["name"] for t in app]
    assert len(names) == len(set(names))
    assert "open_app" not in names and "look_at_screen" not in names
    assert tools.client_tools() <= set(names)
    # Any other scope gets the phone's list too (fail closed), and the PC's list is untouched.
    assert [t["name"] for t in tools.session_tools("siri")] == names
    assert tools.session_tools() == tools.session_tools("pc")
    [pc_url] = [t for t in tools.session_tools() if t["name"] == "open_url"]
    assert pc_url["description"].startswith("Open a website in the browser on this PC")


def test_unknown_tool_and_errors_come_back_as_messages():
    assert tools.run_tool("teleport", {})["ok"] is False
    out = tools.run_tool("schedule", {"kind": "reminder", "title": "x", "text": "x"})
    assert out["ok"] is False and "délai" in out["error"]
    assert tools.run_tool("delegate_to_claude", {"title": "x", "prompt": ""})["ok"] is False


def test_reminder_round_trip(published):
    out = tools.run_tool("schedule", {"kind": "reminder", "title": "Thé", "text": "Le thé est prêt",
                                      "delay_minutes": 4})
    assert out["ok"] and "Thé" in out["scheduled"]
    assert any("Thé" in line for line in tools.run_tool("get_status", {})["upcoming"])
    assert tools.run_tool("cancel_schedule", {"query": "thé"})["cancelled"] == ["Thé"]


def test_memory_reaches_the_instructions(published):
    tools.run_tool("remember", {"fact": "Monsieur aime le jazz"})
    text = instructions.build_instructions(recent="monsieur : et la météo ?")
    assert "Monsieur aime le jazz" in text
    assert "et la météo ?" in text
    assert "Nous sommes" in text
    assert tools.run_tool("forget", {"query": "jazz"})["ok"]
    assert "jazz" not in instructions.build_instructions()


def test_monitor_picking(monkeypatch):
    screens = [(0, 0, 1920, 1080), (-1920, 0, 0, 1080), (1920, 0, 3840, 1080)]
    monkeypatch.setattr(desktop, "_monitors", lambda: screens)
    assert desktop._pick_monitor("gauche") == screens[1]
    assert desktop._pick_monitor("right") == screens[2]
    assert desktop._pick_monitor("2") == screens[1]
    assert desktop._pick_monitor("9") is None
    assert desktop._pick_monitor("primary") == screens[0]


def test_unknown_system_action_does_nothing():
    assert desktop.system_action("fly")["ok"] is False


def test_family_blocks_reach_the_instructions(monkeypatch):
    monkeypatch.setattr(tools.tools_pc, "instructions_block", lambda: "# Bloc PC")
    assert "# Bloc PC" in instructions.build_instructions()
    monkeypatch.setattr(tools.tools_pc, "available", lambda: False)
    assert "# Bloc PC" not in instructions.build_instructions()


def test_delegate_passes_the_profile_through(monkeypatch):
    seen = []

    def create_task(title, prompt, profile, complexity, continue_task, **extra):
        seen.append(profile)
        return {"id": "t1", "profile": "complet", "model": ""}

    monkeypatch.setattr(tools.tasks, "create_task", create_task)
    tools.run_tool("delegate_to_claude", {"title": "x", "prompt": "y"})
    tools.run_tool("delegate_to_claude", {"title": "x", "prompt": "y", "profile": "lecture"})
    assert seen == [None, "lecture"]  # tasks decides what a missing profile means
