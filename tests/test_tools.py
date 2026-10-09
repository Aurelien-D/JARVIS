from jarvis import desktop, tools


def test_every_tool_is_handled_somewhere():
    names = [t["name"] for t in tools.TOOLS]
    assert len(names) == len(set(names))
    assert set(names) == set(tools.HANDLERS) | tools.CLIENT_TOOLS
    assert not set(tools.HANDLERS) & tools.CLIENT_TOOLS


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
    text = tools.build_instructions(recent="monsieur : et la météo ?")
    assert "Monsieur aime le jazz" in text
    assert "et la météo ?" in text
    assert "Nous sommes" in text
    assert tools.run_tool("forget", {"query": "jazz"})["ok"]
    assert "jazz" not in tools.build_instructions()


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
