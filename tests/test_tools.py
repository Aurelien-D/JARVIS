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
        seen.append((name, args, ctx.session_id))
        return {"status": "needs_confirmation", "pending_id": "p1"}

    monkeypatch.setattr(confirm, "gate", gate)
    out = tools.run_tool("delegate_to_claude", {"title": "x", "prompt": "y"}, tools.ToolCtx("s1"))
    assert out["status"] == "needs_confirmation"
    assert seen == [("delegate_to_claude", {"title": "x", "prompt": "y"}, "s1")]
    assert not tools.tasks.TASKS  # parked, not started


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
