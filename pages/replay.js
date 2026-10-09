// GitHub Pages replay: the lab UI, fed from a snapshot of real runs instead of the live factory.
// Reads (bots, registry, code, value cards, cost ledger) come from snap/; the event stream is the recorded one;
// asking a bot replays an answer it really gave. Building new monsters needs the local lab (Python, Docker, keys).
(function () {
  window.REPLAY = true;
  const SNAP = "snap/";
  const _fetch = window.fetch.bind(window);
  const json = (o, status = 200) => new Response(JSON.stringify(o), {status, headers: {"Content-Type": "application/json"}});
  let answers = null;
  const loadAnswers = async () => answers || (answers = await (await _fetch(SNAP + "answers.json")).json());
  const now = () => Date.now() / 1000;
  const NOPE = "This page is a recording of real runs. Building new monsters, uploading files and voice input need the local lab: see the README.";

  window.fetch = async (url, opt = {}) => {
    const u = new URL(typeof url === "string" ? url : url.url, location.href);
    const i = u.pathname.indexOf("/api/");
    if (i < 0) return _fetch(url, opt);
    const p = u.pathname.slice(i + 5);
    const method = (opt.method || "GET").toUpperCase();
    if (p === "voice") return new Response("", {status: 404});
    if (method === "GET") {
      const r = await _fetch(SNAP + "api/" + p.replace(/\//g, "__") + ".json");
      return r.ok ? r : json({detail: "not in the recording"}, 404);
    }
    const m = p.match(/^bots\/([^/]+)\/ask$/);
    if (m) {
      const bot = decodeURIComponent(m[1]);
      let q = "";
      try { q = JSON.parse(opt.body || "{}").question || ""; } catch (e) {}
      const list = (await loadAnswers())[bot] || [];
      if (!list.length) return json({detail: "No recorded answer for this bot yet."}, 404);
      const norm = s => (s || "").toLowerCase().replace(/files: folder \S+/g, "").replace(/\W+/g, " ").trim();
      const a = list.find(x => norm(x.question) === norm(q)) || list[list.length - 1];
      replayAsk(bot, q || a.question, a);
      return json({started: true});
    }
    return json({detail: NOPE}, 403);
  };

  function replayAsk(bot, question, a) {
    const steps = [
      {kind: "ask", bot, question, msg: `asked ${bot}: ${question}`},
      {kind: "stage", stage: "understand", msg: `reading the question for ${bot}`},
      {kind: "log", msg: "replay: this is an answer the bot really gave (recorded), not a new run"},
      {kind: "result", workflow: bot, result: a.result, input: a.input, question, ask_tokens: a.ask_tokens || 0,
       run_tokens: a.run_tokens || 0, msg: `${bot} answered (recorded) · ${a.ask_tokens || 0} tok to read the question · ${a.run_tokens || 0} tok to run`},
      {kind: "idle", msg: `ask ${bot} finished`},
    ];
    steps.forEach((e, k) => setTimeout(() => FakeES.push({ts: now(), ...e}), 350 + k * 450));
  }

  class FakeES {
    constructor() {
      this.onmessage = null;
      FakeES.inst = this;
      _fetch(SNAP + "events.json").then(r => r.json()).then(evs => {
        const go = () => { if (!this.onmessage) return setTimeout(go, 20); evs.forEach(e => FakeES.push(e)); };
        go();
      });
    }
    close() {}
    static push(e) { if (FakeES.inst && FakeES.inst.onmessage) FakeES.inst.onmessage({data: JSON.stringify(e)}); }
  }
  window.EventSource = FakeES;

  document.addEventListener("DOMContentLoaded", () => {
    const b = document.createElement("div");
    b.id = "replayBanner";
    b.innerHTML = "▶ <b>Recorded demo</b> · everything here was built live by the agent; ask any monster and it replays an answer it really gave. " +
      "<a href='https://github.com/VatsalBali/Frank_monster' target='_blank' rel='noopener'>Run the live lab →</a>";
    b.style.cssText = "position:fixed;left:50%;bottom:10px;transform:translateX(-50%);z-index:99;background:#1F2A1A;border:1px solid #6C8F5A;color:#DDEBD3;font:12.5px Inter,sans-serif;padding:7px 14px;border-radius:999px;box-shadow:0 6px 20px rgba(0,0,0,.4)";
    document.body.appendChild(b);
    const l = b.querySelector("a"); l.style.color = "#9BD07F";
  });
})();
