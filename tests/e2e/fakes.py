"""Fakes for the end-to-end tests: nothing leaves this PC.

FAKE_RTC and FAKE_SR are init scripts that run in the page before its own
code; FAKE_CLAUDE stands in for the `claude` command.
"""

# WebRTC: a peer connection that "connects" at once and records everything the
# page sends on its data channel (window.__sent). window.__emit(ev) plays a
# Realtime event from OpenAI; window.__types() summarises what was sent.
# Set window.__sctpMaxMessageSize before connecting to shrink the channel's
# message limit (default 256 KiB).
FAKE_RTC = r"""
window.__sent = [];
class FakeDC {
  constructor() { this.readyState = "connecting"; }
  send(s) { window.__sent.push(JSON.parse(s)); }
  close() { this.readyState = "closed"; this.onclose && this.onclose(); }
}
class FakePC {
  constructor() { window.__pcs = (window.__pcs || 0) + 1; this.connectionState = "new"; }
  get sctp() { return { maxMessageSize: window.__sctpMaxMessageSize || 262144 }; }
  addTrack() {}
  createDataChannel() { this.dc = new FakeDC(); window.__dc = this.dc; return this.dc; }
  async createOffer() { return { type: "offer", sdp: "fake-offer" }; }
  async setLocalDescription() {}
  async setRemoteDescription() {
    setTimeout(() => { this.connectionState = "connected"; this.dc.readyState = "open"; this.dc.onopen && this.dc.onopen(); }, 50);
  }
  close() { this.connectionState = "closed"; }
}
window.RTCPeerConnection = FakePC;
window.__emit = (ev) => window.__dc.onmessage({ data: JSON.stringify(ev) });
window.__types = () => window.__sent.map(m => m.type === "conversation.item.create"
  ? m.item.type + (m.item.content ? ":" + m.item.content[0].type : "") : m.type);
window.__texts = () => window.__sent.filter(m => m.item && m.item.content).map(m => m.item.content[0].text);
"""

# Speech recognition for the wake word: window.__say(text, final) plays a result
# on the recogniser currently listening (window.__rec).
FAKE_SR = r"""
window.__recs = [];
class FakeSR {
  constructor() { window.__recs.push(this); this.running = false; }
  start() { this.running = true; window.__rec = this; }
  abort() { this.running = false; }
  stop() { this.running = false; }
}
window.SpeechRecognition = FakeSR;
window.__say = (text, final) => {
  const alt = [{ transcript: text }]; alt.isFinal = final;
  window.__rec.onresult({ resultIndex: 0, results: [alt] });
};
"""

# OpenAI's answer to the SDP offer (the fake peer connection ignores it).
SDP_ANSWER = "v=0 fake-answer"

# `claude -p --output-format stream-json`: one web search, then a short result.
FAKE_CLAUDE = r'''
import json, sys, time
prompt = sys.stdin.read()
def emit(obj):
    print(json.dumps(obj), flush=True)
emit({"type": "system", "subtype": "init", "session_id": "s1"})
time.sleep(1)
emit({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "WebSearch", "input": {"query": "meteo Lyon"}}]}})
time.sleep(1.5)
emit({"type": "result", "subtype": "success", "is_error": False, "session_id": "s1",
      "result": "Il fait 18 degres a Lyon."})
'''
