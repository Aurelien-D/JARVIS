"""jarvis/page.py: the JARVIS page and the pairing page, with their placeholders
replaced and every value html-escaped."""
import re

from jarvis import page


def test_index_html_replaces_every_placeholder():
    html = page.index_html("jeton-123")
    assert '<meta name="jarvis-token" content="jeton-123">' in html
    assert '<meta name="jarvis-remote" content="0">' in html
    assert '<meta name="jarvis-origin" content="pc">' in html
    assert "__JARVIS_" not in html


def test_index_html_for_a_remote_page():
    html = page.index_html("jeton-tel", remote=True, origin="app:d_0123456789abcdef")
    assert '<meta name="jarvis-remote" content="1">' in html
    assert '<meta name="jarvis-origin" content="app:d_0123456789abcdef">' in html
    assert "__JARVIS_" not in html


def test_values_are_html_escaped():
    html = page.index_html('"><script>alert(1)</script>', origin='app:"><img src=x onerror=alert(1)>')
    assert "<script>alert(1)</script>" not in html and "<img src=x" not in html
    assert 'content="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in html
    assert 'content="app:&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"' in html
    assert "__JARVIS_" not in html


def test_pairing_html_shows_a_known_state():
    for state in page.PAIR_STATES:
        html = page.pairing_html(state)
        assert f'<meta name="jarvis-pair-state" content="{state}">' in html
        assert "__PAIR_STATE__" not in html


def test_unknown_pair_state_is_shown_as_refused():
    for state in ("", "ouvert", '"><script>x</script>', None, "PAIR"):
        html = page.pairing_html(state)
        assert '<meta name="jarvis-pair-state" content="refused">' in html
        assert "__PAIR_STATE__" not in html and "<script>x" not in html


def test_the_pairing_page_never_carries_the_page_token_nor_the_app():
    html = page.pairing_html("pair")
    assert "jarvis-token" not in html and "__JARVIS_TOKEN__" not in html
    assert not re.search(r"main\.js", html)
    assert re.search(r"<title>JARVIS</title>", html)
