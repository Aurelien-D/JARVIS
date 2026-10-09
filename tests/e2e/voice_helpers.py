"""Shared by test_voice.py and test_composer.py: driving the voice session
through the fakes, and a JARVIS page whose clock the test controls."""
import pytest

from fakes import FAKE_RTC, FAKE_SR, SDP_ANSWER


def emit(page, event):
    """A Realtime event from OpenAI."""
    page.evaluate("ev => __emit(ev)", event)


def go_live(page):
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")


def types(page):
    return page.evaluate("__types()")


def sent(page, kind):
    """The client events of one type sent so far."""
    return page.evaluate("t => __sent.filter(m => m.type === t)", kind)


def items(page):
    """conversation.item.create items sent so far."""
    return page.evaluate("__sent.filter(m => m.type === 'conversation.item.create').map(m => m.item)")


def listen(page, *names):
    """Record bus events into window.__bus[name]."""
    page.evaluate("""names => { window.__bus = window.__bus || {};
      for (const n of names) { __bus[n] = []; __jarvis.bus.on(n, d => __bus[n].push(d)); } }""", list(names))


def heard(page, name):
    return page.evaluate("n => __bus[n]", name)


def record_requests(page, pattern, answer=None, status=200):
    """Answer the page's own API calls matching pattern; returns the bodies received."""
    bodies = []

    def handle(route):
        bodies.append(route.request.post_data_json)
        route.fulfill(status=status, json=answer if answer is not None else {"ok": True})

    page.route(pattern, handle)
    return bodies


@pytest.fixture
def clock_jarvis(request):
    """The JARVIS page with every fake, and page.clock installed before it loads."""
    pytest.importorskip("pytest_playwright", reason="pip install -r requirements-dev.txt")
    app_server = request.getfixturevalue("app_server")
    context = request.getfixturevalue("context")
    context.set_default_timeout(10_000)
    context.add_init_script(FAKE_RTC + FAKE_SR)
    context.route("https://api.openai.com/**", lambda route: route.fulfill(
        status=201, body=SDP_ANSWER, headers={"Content-Type": "application/sdp"}))
    context.clock.install()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(app_server.url)
    page.wait_for_function("window.__jarvis && __jarvis.state.ready && __jarvis.state.synced")
    yield page
    assert not errors, f"erreurs dans la page : {errors}"
