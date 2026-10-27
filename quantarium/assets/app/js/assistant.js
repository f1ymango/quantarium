// The assistant panel: chat with your own model, docked beside every page.
// Replies are plain text; references like [[cell:ID]] render as Quantarium
// components. Nothing a model writes is ever inserted as HTML.
import { html, useEffect, useRef, useState, Icon, ErrorNote } from "./ui.js";
import { get, command } from "./api.js";
import { CellCard, statRanges } from "./pages/home.js";

const WIDTH_KEY = "quantarium.assistantWidth";
const MIN_WIDTH = 340;

function storedWidth() {
  try { return Number(localStorage.getItem(WIDTH_KEY)) || 420; } catch { return 420; }
}
function storeWidth(width) {
  try { localStorage.setItem(WIDTH_KEY, String(Math.round(width))); } catch { /* storage may be unavailable */ }
}
const clampWidth = (width) => Math.max(MIN_WIDTH, Math.min(width, Math.round(innerWidth * 0.9)));

const REFERENCE = /\[\[(cell|job|proposal|scenario):([^\]\s]{1,200})\]\]/g;

function CellReference({ id }) {
  const [card, setCard] = useState(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => { get(`cells/${id}`).then((cell) => setCard(cell.card), () => setMissing(true)); }, [id]);
  if (missing) return html`<span class="badge neutral">unknown cell ${id}</span>`;
  if (!card) return html`<span class="faint">…</span>`;
  return html`<div class="assistant-card"><${CellCard} card=${card} ranges=${statRanges([card])} /></div>`;
}

function JobReference({ id }) {
  const [job, setJob] = useState(null);
  useEffect(() => { get(`jobs/${id}`).then(setJob, () => setJob({ status: "unknown" })); }, [id]);
  return html`<a class="badge neutral" href="#/activity">job ${job?.action || id.slice(0, 8)}: ${job?.status || "…"}</a>`;
}

function Text({ value }) {
  // Fenced code first, then references inside the prose.
  return value.split(/```[a-zA-Z]*\n?/).map((part, index) => {
    if (index % 2) return html`<pre class="assistant-code">${part}</pre>`;
    const pieces = [];
    let last = 0;
    for (const match of part.matchAll(REFERENCE)) {
      pieces.push(part.slice(last, match.index));
      const [, kind, id] = match;
      pieces.push(kind === "cell" ? html`<${CellReference} id=${id} />`
        : kind === "job" ? html`<${JobReference} id=${id} />`
        : kind === "proposal" ? html`<a class="badge warn" href="#/inbox">proposal waiting in the Inbox</a>`
        : html`<a class="badge neutral" href=${`#/scenarios/view?key=${encodeURIComponent(id)}`}>${id}</a>`);
      last = match.index + match[0].length;
    }
    pieces.push(part.slice(last));
    return html`<p class="assistant-text">${pieces}</p>`;
  });
}

function ToolStep({ use, result }) {
  const [open, setOpen] = useState(false);
  const proposal = use.name.startsWith("propose_");
  return html`<div class=${`assistant-tool ${result?.is_error ? "failed" : ""}`}>
    <button class="ghost" onClick=${() => setOpen(!open)} aria-expanded=${open}>
      ${result ? (result.is_error ? "✕" : "✓") : "…"} ${use.name}${result?.is_error ? " (refused or failed)" : ""}</button>
    ${proposal && result && !result.is_error && html`<a class="badge warn" href="#/inbox">Waiting for you in the Inbox</a>`}
    ${open && html`<pre class="assistant-code">${JSON.stringify(use.input, null, 1)}</pre>
      ${result && html`<pre class="assistant-code">${result.content}</pre>`}`}
  </div>`;
}

function Messages({ chat }) {
  const results = {};
  for (const message of chat.messages) {
    for (const block of message.content) if (block.type === "tool_result") results[block.tool_use_id] = block;
  }
  return chat.messages.map((message) => {
    if (message.role === "user" && message.content[0]?.type === "tool_result") return null;
    if (message.role === "user") {
      return html`<div class="assistant-message user">${message.content[0].text}</div>`;
    }
    return html`<div class="assistant-message model">${message.content.map((block) =>
      block.type === "text" ? html`<${Text} value=${block.text} />`
        : block.type === "tool_use" ? html`<${ToolStep} use=${block} result=${results[block.id]} />` : null)}</div>`;
  });
}

const APP_NAME = { "codex-exec": "Codex", "claude-code": "Claude Code" };

export function AssistantPanel() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState(null);
  const [chats, setChats] = useState([]);
  const [chat, setChat] = useState(null);
  const [connectionId, setConnectionId] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState(null);
  const list = useRef(null);
  const [width, setWidth] = useState(() => clampWidth(storedWidth()));
  const wide = width >= Math.round(innerWidth * 0.6);
  // Drag the left edge to resize; the width is remembered in this browser.
  const startResize = (event) => {
    event.preventDefault();
    const move = (moved) => setWidth(clampWidth(innerWidth - moved.clientX));
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      setWidth((current) => { storeWidth(current); return current; });
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  };
  const toggleWide = () => {
    const next = wide ? 420 : Math.round(innerWidth * 0.66);
    setWidth(clampWidth(next)); storeWidth(clampWidth(next));
  };

  const connections = (status?.connections || []).filter((item) => !item.revoked_utc && item.kind !== "external");
  const refresh = async () => {
    try {
      const next = await get("agents");
      setStatus(next);
      if (next.enabled) setChats(await get("agents/chats"));
    } catch (problem) { setError(problem); }
  };
  useEffect(() => { if (open) refresh(); }, [open]);
  useEffect(() => { if (!connectionId && connections[0]) setConnectionId(connections[0].id); }, [connections.length]);
  // While the model works, follow the conversation.
  useEffect(() => {
    if (!chat?.running) return undefined;
    const timer = setInterval(async () => {
      try { setChat(await get(`agents/chats/${chat.id}`)); } catch (problem) { setError(problem); }
    }, 700);
    return () => clearInterval(timer);
  }, [chat?.id, chat?.running]);
  useEffect(() => { list.current?.scrollTo(0, list.current.scrollHeight); }, [chat?.messages?.length]);

  const openChat = async (id) => { setError(null); setChat(id ? await get(`agents/chats/${id}`) : null); };
  const send = async () => {
    setError(null);
    try {
      let current = chat;
      if (!current) current = await command("chat-create", { connection_id: connectionId });
      await command("chat-send", { id: current.id, text: draft, page: location.hash || "#/home" });
      setDraft("");
      setChat(await get(`agents/chats/${current.id}`));
      setChats(await get("agents/chats"));
    } catch (problem) { setError(problem); }
  };
  const stop = async () => { try { await command("chat-stop", { id: chat.id }); } catch (problem) { setError(problem); } };
  const [deleting, setDeleting] = useState(false);
  const [alsoCodex, setAlsoCodex] = useState(false);
  const remove = async () => {
    try {
      await command("chat-delete", { id: chat.id, app_transcript: alsoCodex });
      setDeleting(false); setAlsoCodex(false); setChat(null); setChats(await get("agents/chats"));
    } catch (problem) { setError(problem); }
  };

  if (!open) {
    return html`<button class="assistant-launcher primary" onClick=${() => setOpen(true)} aria-label="Open the assistant">
      <${Icon} name="agents" size=${18} /> Assistant</button>`;
  }
  const ready = status?.enabled && connections.length;
  return html`<aside class="assistant-panel" aria-label="Assistant" style=${`width:${width}px`}>
    <div class="assistant-resize" onPointerDown=${startResize} role="separator" aria-orientation="vertical"
      aria-label="Resize the assistant" title="Drag to resize"
      tabindex="0" onKeyDown=${(event) => {
        const step = event.key === "ArrowLeft" ? 40 : event.key === "ArrowRight" ? -40 : 0;
        if (step) { const next = clampWidth(width + step); setWidth(next); storeWidth(next); }
      }}></div>
    <div class="assistant-head row between">
      <strong>Assistant</strong>
      <div class="row" style="gap:6px">
        ${ready && html`<select value=${chat?.id || ""} onChange=${(event) => openChat(event.target.value)} aria-label="Chats">
          <option value="">New chat</option>${chats.map((item) => html`<option value=${item.id}>${item.title}</option>`)}</select>`}
        ${chat && !chat.running && html`<button class="ghost" onClick=${() => setDeleting(!deleting)} aria-label="Delete this chat"><${Icon} name="trash" size=${16} /></button>`}
        <button class="ghost" onClick=${toggleWide} aria-label=${wide ? "Narrow the assistant" : "Widen the assistant"}
          title=${wide ? "Narrow" : "Widen"}><${Icon} name=${wide ? "collapse" : "expand"} size=${16} /></button>
        <button class="ghost" onClick=${() => setOpen(false)} aria-label="Close the assistant"><${Icon} name="close" size=${16} /></button></div>
    </div>
    ${!status ? html`<p class="muted">…</p>`
      : !status.enabled ? html`<div class="assistant-empty"><p>Agent features are off.</p><a href="#/agents" onClick=${() => setOpen(false)}>Turn them on in Agents</a></div>`
      : !connections.length ? html`<div class="assistant-empty"><p>Connect a model first: a model API, or an agent app such as Codex or Claude Code.</p><a href="#/agents" onClick=${() => setOpen(false)}>Add an assistant connection</a></div>`
      : html`
        ${!chat && html`<label class="assistant-connection">Model
          <select value=${connectionId} onChange=${(event) => setConnectionId(event.target.value)}>
            ${connections.map((item) => html`<option value=${item.id}>${item.name}</option>`)}</select></label>`}
        ${deleting && chat && html`<div class="notice assistant-delete">
          <p style="margin:0 0 6px">Delete this chat from Quantarium?${chat.bridge ? " The agent app's temporary folder is removed too." : ""}</p>
          ${chat.bridge && html`<p class="hint" style="margin:0 0 6px">${APP_NAME[chat.app_profile] || "The agent app"} may keep its own transcript of this chat${chat.app_transcripts?.length ? html`, in <span class="mono">${chat.app_transcripts[0]}</span>` : ""}. The provider's copy follows your plan's terms.</p>
            ${chat.app_transcripts?.length > 0 && html`<label class="agent-tier"><input type="checkbox" checked=${alsoCodex} onChange=${() => setAlsoCodex(!alsoCodex)} />
              <span>Also delete ${APP_NAME[chat.app_profile] || "the app"}'s transcript of this chat</span></label>`}`}
          <div class="row" style="gap:6px;justify-content:flex-end"><button class="ghost" onClick=${() => setDeleting(false)}>Cancel</button>
            <button class="primary" onClick=${remove}>Delete</button></div></div>`}
        <div class="assistant-messages" ref=${list}>
          ${chat ? html`<${Messages} chat=${chat} />` : html`<p class="muted">Ask about your cells and results, have it draft and test strategies, or prepare a Feed test for you to approve. It works with ${connections.find((item) => item.id === connectionId)?.tiers.join(", ")} permissions.</p>`}
          ${chat?.running && html`<p class="faint">Working…</p>`}
          ${chat?.status === "error" && html`<div class="notice bad">${chat.error}</div>`}
        </div>
        <${ErrorNote} error=${error} />
        <div class="assistant-input">
          <textarea value=${draft} rows="3" placeholder="Message your assistant" onInput=${(event) => setDraft(event.target.value)}
            onKeyDown=${(event) => event.key === "Enter" && !event.shiftKey && draft.trim() && !chat?.running && (event.preventDefault(), send())}></textarea>
          <div class="row between"><span class="hint">${chat ? `${chat.connection_name} · ${chat.usage.input_tokens + chat.usage.output_tokens} tokens` : ""}</span>
            ${chat?.running ? html`<button onClick=${stop}>Stop</button>`
              : html`<button class="primary" disabled=${!draft.trim()} onClick=${send}>Send</button>`}</div>
        </div>`}
  </aside>`;
}
