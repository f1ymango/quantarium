// First-run tutorial: a guided quest through the real app.
//
// The user builds a scenario, hatches a cell, trains it in Static, sends it
// into the Feed arena, then reviews the Log and Activity, ending on Home with
// that cell. Steps run in the tutorial's own, initially empty
// workspace (see quantarium/onboarding.py), which finishing adds to the user's. "do" steps finish when the
// app's real state shows the action happened; "talk" steps explain.
import { html, useEffect, useRef, useState, navigate } from "../ui.js";
import { get, command, runJob } from "../api.js";
import { setTutorialState, onTutorialState, TUTORIAL_SCENARIO } from "./state.js";
import { CellCard, statRanges } from "../pages/home.js";

const CHAPTERS = [
  { title: "Build a market", text: "Every test needs prices. You'll generate a fictional market with a rally, a crash, and a recovery." },
  { title: "Hatch a cell", text: "A cell is one algorithm with its own account. You'll create yours and give it a stock and a script." },
  { title: "Train", text: "Static replays prices as fast as your computer allows. You'll train your cell on the market you built." },
  { title: "Enter the arena", text: "Feed follows a cell forward, one session at a time. You'll send your cell into the arena and advance it." },
  { title: "The record", text: "Everything you did is kept. You'll find it in the Log and Activity, then return home." },
  { title: "Test it honestly", text: "Freeze a Lab comparison, read its controls and caveats, then see why reusing the same market makes a second attempt exploratory." },
];

const route = () => location.hash.replace(/^#\/?/, "");
const onPage = (prefix) => route() === prefix || route().startsWith(`${prefix}?`) || route().startsWith(`${prefix}/`);
const exists = (selector) => Boolean(document.querySelector(selector));
const nav = (path) => `[data-tour-nav='${path}']`;
const cellRoute = (ctx, tab) => `cells/${ctx.cellId}${tab ? `?tab=${tab}` : ""}`;

async function tutorialCell(ctx) {
  return ctx.cellId ? get(`cells/${ctx.cellId}`).catch(() => null) : null;
}
async function scenarioExists(ctx) {
  const workspace = await get("workspace");
  return workspace.scenarios.find((item) => item.name === ctx.names.scenario) || null;
}
async function experiment(ctx) {
  return (await get("arena")).find((item) => item.id === ctx.names.feed) || null;
}
const TUTORIAL_LAB_ID = "tutorial_first_cell";
async function labExperiment(attempt) {
  return (await get("lab")).find((item) => item.spec.idea === TUTORIAL_LAB_ID &&
    item.attempt_number === attempt) || null;
}
async function registerTutorialExperiment(ctx, attempt) {
  const scenario = await scenarioExists(ctx);
  if (!scenario) throw new Error("The tutorial market is not ready yet.");
  await command("lab-register", {
    idea: TUTORIAL_LAB_ID, name: `${ctx.names.cell} · attempt ${attempt}`,
    cells: [ctx.cellId], scenarios: [scenario.key],
    pass_rule: { min_mean_excess_return_pp: 0, max_drawdown_pct: null, min_sessions: 60 },
    slippage_bps: 0, commission_per_order: 0, commission_per_share: 0,
    commission_minimum: 0, slippage_range_fraction: 0, disguised_replay: false,
  });
  location.reload();
}
async function runTutorialExperiment(attempt) {
  const record = await labExperiment(attempt);
  if (!record || record.status !== "registered") throw new Error("The frozen tutorial experiment is not ready to run.");
  await runJob("lab-run", { id: record.id });
  location.reload();
}
const spec = (symbols, sleeves) => ({ mode: "simple", symbols, initial_cash: 10000, sleeves,
  rebalance_every_sessions: 1, max_position_weight: 1, min_cash_weight: 0, max_previous_volume_participation: null });
// The tutorial's copy of the starter strategies; its path is in this run's names.
const trend = (ctx) => ({ script: ctx.names.script, class: "TrendFollower", allocation: 1, parameters: { window: 20 } });
const waitForVisibleTarget = async (selector, timeoutMs = 5000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const element = document.querySelector(selector);
    if (element && element.getClientRects().length) return element;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("The tutorial control is still loading. Try again, or use the highlighted control.");
};
const clickTarget = (selector) => async () => {
  const element = await waitForVisibleTarget(selector);
  if (element.disabled || element.getAttribute("aria-disabled") === "true") {
    throw new Error("That control is currently unavailable.");
  }
  element.click();
};

// [chapter, kind, title, text(ctx), target(ctx), done(ctx), options]
const STEPS = [
  // 1 · Build a market
  { chapter: 0, kind: "do", goal: "Open Scenario", title: "Open Scenario",
    text: () => "Scenarios are markets you can test against. Open the Scenario page.",
    target: () => nav("scenarios"), done: () => onPage("scenarios"), auto: clickTarget(nav("scenarios")) },
  { id: "open-generate", chapter: 0, kind: "do", goal: "Generate a market", title: "Generate a market", page: "scenarios",
    text: () => "Select Generate to design a market.",
    target: () => "[data-tour='scenario-generate']", done: () => exists("[data-tour='scenario-form']"),
    auto: clickTarget("[data-tour='scenario-generate']") },
  { chapter: 0, kind: "talk", title: "Your market's recipe", resume: "open-generate",
    text: (ctx) => `It's filled in for you: ${ctx.names.scenario} has one stock, AAA. Its three segments rise about a quarter over 90 sessions, crash by about as much in just 15, then recover. The seed makes the same prices every time.`,
    target: () => ".dialog" },
  { chapter: 0, kind: "do", goal: "Create the prices", title: "Create the prices", resume: "open-generate",
    text: () => "Adjust the recipe if you like, then select Generate. Quantarium builds the prices and saves them as a scenario.",
    target: () => ".dialog", done: async (ctx) => Boolean(await scenarioExists(ctx)),
    auto: async (ctx) => {
      const seg = TUTORIAL_SCENARIO.segments;
      await runJob("scenario-generate", { output: `scenarios/${ctx.names.scenario}`, spec: {
        name: ctx.names.scenario, description: TUTORIAL_SCENARIO.description, seed: TUTORIAL_SCENARIO.seed,
        start_date: TUTORIAL_SCENARIO.start_date, symbols: ["AAA"], tags: ["tutorial", "crash"],
        segments: seg.map((item) => ({ sessions: item.sessions, annual_drift: item.drift / 100, annual_volatility: item.volatility / 100 })) } });
      location.reload();
    } },
  { chapter: 0, kind: "talk", title: "A market of your own", page: "scenarios",
    text: (ctx) => `Here it is. The card shows ${ctx.names.scenario}'s price path. Its synthetic badge is a reminder: these prices were generated, not observed, so results on them show how a strategy reacts to this shape, not how it did in real markets.`,
    target: (ctx) => `[data-tour='scenario-card-${ctx.names.scenario}']` },

  // 2 · Hatch a cell
  { chapter: 1, kind: "do", goal: "Go Home", title: "Go Home", text: () => "Home is where your cells live. Open Home.",
    target: () => nav("home"), done: () => onPage("home"), auto: clickTarget(nav("home")) },
  { chapter: 1, kind: "do", goal: "Add a cell", title: "Add a cell", page: "home",
    text: () => "Select Add cell. A new, unnamed cell appears and opens.",
    target: () => "[data-tour='home-add']",
    done: (ctx) => {
      const match = /^cells\/(c_[0-9a-f]{10})/.exec(route());
      if (match && match[1] !== ctx.cellId) ctx.setCellId(match[1]);
      return Boolean(match);
    },
    auto: async () => { const record = await command("cell-create"); navigate(`cells/${record.id}?tab=configure`); } },
  { chapter: 1, kind: "do", goal: "Name it", title: "Name your cell", page: (ctx) => cellRoute(ctx, "configure"),
    text: (ctx) => `Click the name at the top, type ${ctx.names.cell}, and press Enter.`,
    target: () => "[data-tour='cell-name']",
    done: async (ctx) => { const cell = await tutorialCell(ctx); return Boolean(cell && !cell.name.startsWith("Unnamed cell")); },
    auto: async (ctx) => { await command("cell-save", { id: ctx.cellId, name: ctx.names.cell }); location.reload(); } },
  { chapter: 1, kind: "do", goal: "Give it a stock", title: "Give it a stock", page: (ctx) => cellRoute(ctx, "configure"),
    text: () => "This is a Simple cell: one stock, one script. Type AAA in the Stock box and press Enter.",
    target: () => "[data-tour='config-symbols']",
    done: async (ctx) => [...document.querySelectorAll(".symbol-chip")].some((chip) => chip.textContent.startsWith("AAA"))
      || Boolean((await tutorialCell(ctx))?.spec.symbols.includes("AAA")),
    auto: async (ctx) => { const cell = await tutorialCell(ctx); await command("cell-save", { id: ctx.cellId, spec: { ...cell.spec, symbols: ["AAA"] } }); } },
  { chapter: 1, kind: "do", goal: "Choose a script", title: "Choose its brain", page: (ctx) => cellRoute(ctx, "configure"),
    text: () => "Scripts decide what a cell holds. Select Add script, then choose TrendFollower: it holds AAA while the price is above its 20-session average and moves to cash when it isn't.",
    target: () => "[data-tour='config-scripts']",
    done: async (ctx) => [...document.querySelectorAll("[data-tour='config-scripts'] select")].some((select) => select.value.endsWith("|TrendFollower"))
      || Boolean((await tutorialCell(ctx))?.spec.sleeves.some((sleeve) => sleeve.class === "TrendFollower")),
    auto: async (ctx) => { await command("cell-save", { id: ctx.cellId, spec: spec(["AAA"], [trend(ctx)]) }); } },
  { chapter: 1, kind: "do", goal: "Save it", title: "Save your cell", page: (ctx) => cellRoute(ctx, "configure"),
    text: () => "Select Save. Quantarium checks the cell; once it's ready, it can run.",
    target: () => "[data-tour='config-save']",
    done: async (ctx) => {
      const cell = await tutorialCell(ctx);
      return Boolean(cell && !cell.card.problems.length && cell.spec.sleeves.some((sleeve) => sleeve.class === "TrendFollower"));
    },
    auto: async (ctx) => { await command("cell-save", { id: ctx.cellId, spec: spec(["AAA"], [trend(ctx)]) }); location.reload(); } },
  { chapter: 1, kind: "talk", title: "It's alive", page: (ctx) => cellRoute(ctx, "configure"),
    text: (ctx) => `${ctx.names.cell} is ready. It has its own $10,000 account that no other cell can touch. The Run button tests it any time, but first, take it to the training ground.`,
    target: () => "[data-tour='cell-run']" },

  // 3 · Train
  { chapter: 2, kind: "do", goal: "Open Static", title: "Open Static", text: () => "Static is the training ground. Open it.",
    target: () => nav("static"), done: () => onPage("static"), auto: clickTarget(nav("static")) },
  { id: "open-train", chapter: 2, kind: "do", goal: "Pick your cell", title: "Pick a cell to train", page: "static",
    text: () => "Select Test a cell.", target: () => "[data-tour='static-test']",
    done: (ctx) => exists(`[data-tour='pick-${ctx.cellId}']`), auto: clickTarget("[data-tour='static-test']") },
  { chapter: 2, kind: "do", goal: "Pick your cell", title: "Choose your cell", resume: "open-train",
    text: (ctx) => `Choose ${ctx.names.cell}.`, target: (ctx) => `[data-tour='pick-${ctx.cellId}']`,
    done: () => exists("[data-tour='deploy-run']"), auto: (ctx) => clickTarget(`[data-tour='pick-${ctx.cellId}']`)() },
  { chapter: 2, kind: "do", goal: "Train on your market", title: "Train on your market", resume: "open-train",
    text: (ctx) => `Test across scenarios is chosen, with ${ctx.names.scenario} ticked. Select Run. Quantarium replays all 200 sessions in a moment.`,
    target: () => ".dialog",
    done: async (ctx) => Boolean((await tutorialCell(ctx))?.card.deployments.suite),
    auto: clickTarget("[data-tour='deploy-run']") },
  { chapter: 2, kind: "talk", title: "Trained", page: "static",
    text: (ctx) => `${ctx.names.cell} finished its first training run. Its card shows the result: return, drawdown (its worst fall from a peak), Sharpe, and how many sessions it lived through.`,
    target: (ctx) => `[data-tour='card-${ctx.names.cell}']` },
  { chapter: 2, kind: "do", goal: "Open its results", title: "Look closer", page: "static",
    text: () => "Open the card to see what your cell did, trade by trade.",
    target: (ctx) => `[data-tour='card-${ctx.names.cell}']`,
    done: (ctx) => route() === `cells/${ctx.cellId}` || route().startsWith(`cells/${ctx.cellId}?tab=overview`),
    auto: async (ctx) => navigate(`cells/${ctx.cellId}`) },
  { chapter: 2, kind: "talk", title: "The scoreboard", page: (ctx) => cellRoute(ctx),
    text: () => "Return is how much the account grew; max drawdown is its worst fall from a peak; volatility and Sharpe describe how bumpy the ride was.",
    target: () => "[data-tour='cell-metrics']" },
  { chapter: 2, kind: "talk", title: "Watch it think", page: (ctx) => cellRoute(ctx),
    text: () => "▲ marks a buy and ▼ a sell. Look around the crash: the trend follower sells as the price falls below its average, then buys back as it recovers.",
    target: () => "[data-tour='cell-holdings']" },

  // 4 · Enter the arena
  { chapter: 3, kind: "do", goal: "Open Feed", title: "Open the arena", text: () => "Feed is the arena. Open it.",
    target: () => nav("feed"), done: () => onPage("feed"), auto: clickTarget(nav("feed")) },
  { id: "open-import", chapter: 3, kind: "do", goal: "Import your cell", title: "Import from Static", page: "feed",
    text: () => "Select Import from Static. The cell keeps exactly the settings it trained with.",
    target: () => "[data-tour='feed-import']",
    done: (ctx) => exists(`[data-tour='pick-${ctx.cellId}']`), auto: clickTarget("[data-tour='feed-import']") },
  { chapter: 3, kind: "do", goal: "Import your cell", title: "Choose your cell", resume: "open-import",
    text: (ctx) => `Choose ${ctx.names.cell}.`, target: (ctx) => `[data-tour='pick-${ctx.cellId}']`,
    done: () => exists("[data-tour='deploy-run']"), auto: (ctx) => clickTarget(`[data-tour='pick-${ctx.cellId}']`)() },
  { chapter: 3, kind: "do", goal: "Start the experiment", title: "Start a forward test", resume: "open-import",
    text: (ctx) => `A practice experiment, ${ctx.names.feed}, is set up on your market, releasing sessions only through June 28, 2030. Select Run. Your cell starts there and meets the rest later.`,
    target: () => ".dialog",
    done: async (ctx) => Boolean(await experiment(ctx)), auto: clickTarget("[data-tour='deploy-run']") },
  { chapter: 3, kind: "talk", title: "In the arena", page: "feed",
    text: () => "The practice · scenario badge means its sessions come from your generated market. With real prices, these would be days your cell had never seen when it was frozen: a genuine forward test.",
    target: (ctx) => `[data-tour='feed-experiment-${ctx.names.feed}']` },
  { chapter: 3, kind: "do", goal: "Advance", title: "Let time pass", page: "feed",
    text: () => "Select Advance. It processes every session the experiment hasn't seen yet, like new trading days arriving.",
    target: (ctx) => `[data-tour='feed-advance-${ctx.names.feed}']`,
    done: async (ctx) => {
      const [found, scenario] = await Promise.all([experiment(ctx), scenarioExists(ctx)]);
      return Boolean(found && scenario && found.last_date === scenario.end_date);
    },
    auto: (ctx) => clickTarget(`[data-tour='feed-advance-${ctx.names.feed}']`)() },
  { chapter: 3, kind: "talk", title: "And Live Feed?", page: "feed",
    text: () => "Feed Batch moves a day at a time. Live Feed follows one-minute bars during a single market session, from a data provider such as Alpaca, and starts from the command line. You won't need it for now, but its results land in the same Log.",
    target: () => "[data-tour='feed-live']" },

  // 5 · The record
  { chapter: 4, kind: "do", goal: "Open the Log", title: "Open the Log", text: () => "Every run is recorded. Open Log.",
    target: () => nav("log"), done: () => onPage("log"), auto: clickTarget(nav("log")) },
  { chapter: 4, kind: "talk", title: "The Feed log", page: "log",
    text: (ctx) => `Your experiment ${ctx.names.feed} is listed here, with every Feed experiment and any Live Feed result.`,
    target: () => "[data-tour='log-title']" },
  { chapter: 4, kind: "do", goal: "See Static runs", title: "Switch to Static", page: "log",
    text: () => "Select Static to see training runs and scenario suites.",
    target: () => "[data-tour='log-tab-static']", done: () => route().includes("view=static"),
    auto: async () => { location.hash = "#/log?view=static"; } },
  { chapter: 4, kind: "do", goal: "Open Activity", title: "Open Activity", text: () => "Now see the jobs behind those results. Open Activity.",
    target: () => nav("activity"), done: () => onPage("activity"), auto: clickTarget(nav("activity")) },
  { chapter: 4, kind: "talk", title: "Every job", page: "activity",
    text: () => "Each thing you started (the market, the training run, the experiment, Advance) ran as a job, one at a time. This list lasts until the server stops; the results themselves are saved for good.",
    target: () => "[data-tour='activity-jobs']" },
  { chapter: 4, kind: "do", goal: "Go home", title: "Head home", text: () => "One last stop. Open Home.",
    target: () => nav("home"), done: () => onPage("home"), auto: clickTarget(nav("home")) },
  { chapter: 4, kind: "talk", title: "Your first cell", page: "home",
    text: (ctx) => `This is ${ctx.names.cell}: hatched, trained, and tested in the arena. Its card shows its newest result. From here, build more cells, try other scripts, or test them across harsher markets.`,
    target: (ctx) => `[data-tour='card-${ctx.names.cell}']` },

  // 6 · Test it honestly
  { chapter: 5, kind: "do", goal: "Open the Lab", title: "Open the Lab", text: () => "The Lab freezes a test before running it. Open the Lab.",
    target: () => nav("lab"), done: () => onPage("lab"), auto: clickTarget(nav("lab")) },
  { chapter: 5, kind: "do", goal: "Freeze the first attempt", title: "Register a fair comparison", page: "lab",
    text: (ctx) => `Keep the prefilled idea and ${ctx.names.scenario} market, then select Freeze registration. The cell, rule, market, controls, and costs are pinned before results appear.`,
    target: () => "[data-tour='lab-register']",
    done: async () => (await labExperiment(1))?.status === "registered",
    auto: (ctx) => registerTutorialExperiment(ctx, 1) },
  { chapter: 5, kind: "talk", title: "A test with controls", page: "lab",
    text: () => "The frozen experiment compares your cell with buy-and-hold and equal-weight controls. The pass rule stays at its default: at least 60 sessions and no required excess-return hurdle. These are comparisons on one invented market, not a forecast.",
    target: () => "[data-tour='lab-experiment']" },
  { chapter: 5, kind: "do", goal: "Run the first attempt", title: "Run the frozen test", page: "lab",
    text: () => "Select Run frozen experiment. The Lab will replay the market and show the verdict, statistics, and caveats.",
    target: () => "[data-tour='lab-run']",
    done: async () => (await labExperiment(1))?.status === "completed",
    auto: () => runTutorialExperiment(1) },
  { chapter: 5, kind: "talk", title: "Read the verdict", page: "lab",
    text: () => "Compare the candidate row with both controls. The statistics include the deflated Sharpe ratio, which raises the bar for trying many variants; the caveats explain what this single synthetic scenario cannot establish.",
    target: () => "[data-tour='lab-verdict']" },
  { chapter: 5, kind: "do", goal: "Register a second attempt", title: "Try the same idea again", page: "lab",
    text: (ctx) => `Keep the same idea and ${ctx.names.scenario} market. Select Freeze registration again. Quantarium counts this as another attempt at the same question.`,
    target: () => "[data-tour='lab-register']",
    done: async () => (await labExperiment(2))?.status === "registered",
    auto: (ctx) => registerTutorialExperiment(ctx, 2) },
  { chapter: 5, kind: "talk", title: "The market has been used", page: "lab",
    text: () => "The second registration reuses the same market for the same idea. Its next result will be exploratory: it can help you learn, but it is not fresh confirmation.",
    target: () => "[data-tour='lab-experiment']" },
  { chapter: 5, kind: "do", goal: "Run the exploratory attempt", title: "Run attempt two", page: "lab",
    text: () => "Run the second frozen experiment and inspect the reuse warning in its verdict.",
    target: () => "[data-tour='lab-run']",
    done: async () => {
      const record = await labExperiment(2);
      return record?.status === "completed" &&
        record.result?.verdict?.caveats?.some((item) => item.includes("already used related scenario data"));
    },
    auto: () => runTutorialExperiment(2) },
  { chapter: 5, kind: "talk", title: "Evidence has limits", page: "lab", final: true,
    text: (ctx) => `The second attempt is marked exploratory because it reused ${ctx.names.scenario}. The tutorial keeps this Lab practice private and discards its ledger when you finish; your cell, market, Static result, and Feed practice are kept. In real research, a result matters only within its data, controls, assumptions, and attempt history.`,
    target: () => "[data-tour='lab-reused-data']" },
];

function geometry(element) {
  const box = element.getBoundingClientRect();
  const pad = 7;
  return { x: Math.max(0, box.left - pad), y: Math.max(0, box.top - pad),
    w: Math.min(innerWidth, box.right + pad) - Math.max(0, box.left - pad),
    h: Math.min(innerHeight, box.bottom + pad) - Math.max(0, box.top - pad) };
}

function TitleCard({ onBegin, onSkip, busy }) {
  return html`<div class="tour-screen" role="dialog" aria-modal="true" aria-labelledby="tour-title">
    <div class="tour-card tour-title-card">
      <img class="tour-logo" src="/logo.svg" alt="" aria-hidden="true" />
      <span class="eyebrow">Welcome</span>
      <h1 id="tour-title">Quantarium</h1>
      <p class="tour-lead">A lab for trading algorithms. Each one lives in a <strong>cell</strong> with its own simulated account. Nothing here touches real money.</p>
      <p class="muted">In about seven minutes you'll build a market, train a cell, follow it forward, and examine a controlled test.</p>
      <ol class="tour-chapter-list">${CHAPTERS.map((chapter, index) => html`<li><span>${index + 1}</span>${chapter.title}</li>`)}</ol>
      <div class="row" style="justify-content:center;gap:10px">
        <button class="ghost" disabled=${busy} onClick=${onSkip}>Skip tutorial</button>
        <button class="primary tour-big" disabled=${busy} onClick=${onBegin} data-tour-primary>${busy ? "Preparing…" : "Begin"}</button>
      </div>
    </div></div>`;
}

function ChapterCard({ index, onStart }) {
  const chapter = CHAPTERS[index];
  const goals = [...new Set(STEPS.filter((step) => step.chapter === index && step.kind === "do").map((step) => step.goal))];
  useEffect(() => { document.querySelector(".tour-chapter-card [data-tour-primary]")?.focus(); }, [index]);
  return html`<div class="tour-screen" role="dialog" aria-modal="true" aria-labelledby="tour-chapter">
    <div class="tour-card tour-chapter-card">
      <span class="eyebrow">Chapter ${index + 1} of ${CHAPTERS.length}</span>
      <h1 id="tour-chapter">${chapter.title}</h1>
      <p class="tour-lead">${chapter.text}</p>
      <ul class="tour-goals">${goals.map((goal) => html`<li>◎ ${goal}</li>`)}</ul>
      <button class="primary tour-big" onClick=${onStart} data-tour-primary>Start chapter</button>
    </div></div>`;
}

function Finale({ names, cellId, onClose }) {
  const [card, setCard] = useState(null);
  useEffect(() => { get("cells").then((cards) => setCard(cards.find((item) => item.id === cellId) || null), () => {}); }, [cellId]);
  return html`<div class="tour-screen" role="dialog" aria-modal="true" aria-labelledby="tour-finale">
    <div class="tour-card tour-finale">
      <span class="eyebrow">Tutorial complete</span>
      <h1 id="tour-finale">${names.cell} has hatched</h1>
      <p class="tour-lead">You built a market, trained a cell, followed it forward, and tested it against controls.</p>
      ${card && html`<div class="tour-finale-card"><${CellCard} card=${card} ranges=${statRanges([card])} /></div>`}
      <p class="muted">Replay this tour any time from Learn, or open the circled ? beside a page title for the handbook.</p>
      <button class="primary tour-big" onClick=${onClose} data-tour-primary>Enter Quantarium</button>
    </div></div>`;
}

export function GameTour() {
  const [state, setState] = useState(null);
  const [phase, setPhase] = useState("loading"); // loading | title | chapter | step | finale | off
  const [rect, setRect] = useState(null);
  const [missing, setMissing] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const bubble = useRef(null);
  const target = useRef(null);
  const index = Math.min(state?.step ?? 0, STEPS.length - 1);
  const step = STEPS[index];
  const ctx = {
    names: state?.names || {}, cellId: state?.cell_id || null,
    setCellId: (id) => { command("onboarding-step", { step: index, cell_id: id }).then(apply, () => {}); },
  };

  const apply = (next) => { setState(next); setTutorialState(next); };
  useEffect(() => {
    get("onboarding").then((next) => {
      apply(next);
      setPhase(next.status === "pending" ? "title" : next.status === "active" ? "step" : "off");
    }, () => setPhase("off"));
    // Learn's replay button starts the tutorial without a reload.
    return onTutorialState((next) => {
      setState(next);
      if (next?.status === "active" && next.step === 0) setPhase("chapter");
    });
  }, []);

  const finish = async (status) => {
    setBusy(true); setError(null);
    try {
      apply(await command("onboarding-finish", { status }));
      setPhase(status === "completed" ? "finale" : "off");
    } catch (problem) { setError(problem); }
    setBusy(false);
  };
  const skip = () => { if (confirm("Leave the tutorial? Everything made in it is cleared and your workspace stays as it was. You can replay it any time from Learn.")) finish("skipped"); };
  const move = async (next) => {
    if (next >= STEPS.length) return finish("completed");
    const nextChapter = STEPS[next].chapter !== step.chapter;
    setRect(null); setMissing(false); setCompleted(false); setError(null);
    try { apply(await command("onboarding-step", { step: next })); } catch (problem) { setError(problem); return; }
    setPhase(nextChapter ? "chapter" : "step");
  };
  const begin = async () => {
    setBusy(true); setError(null);
    try { apply(await command("onboarding-start")); setPhase("chapter"); } catch (problem) { setError(problem); }
    setBusy(false);
  };

  // While a step is showing, dialogs dock at the top so the bubble never covers them.
  useEffect(() => {
    document.documentElement.classList.toggle("tour-running", phase === "step");
    return () => document.documentElement.classList.remove("tour-running");
  }, [phase]);

  // Go to the page a step lives on, once, when the step starts.
  useEffect(() => {
    if (phase !== "step" || !state) return;
    const page = typeof step.page === "function" ? step.page(ctx) : step.page;
    if (page && route().split("?")[0] !== page.split("?")[0]) navigate(page);
  }, [phase, index]);

  // Follow the step's target and check whether its objective is done.
  useEffect(() => {
    if (phase !== "step" || !state) return undefined;
    const selector = step.target(ctx);
    if (innerWidth < 768) window.dispatchEvent(new Event(selector.startsWith("[data-tour-nav=") ? "quantarium-tour-open-menu" : "quantarium-tour-close-menu"));
    let misses = 0, checking = false, alive = true, finished = false;
    const tick = async () => {
      const element = document.querySelector(selector);
      if (!element || !element.getClientRects().length) {
        target.current = null; setRect(null);
        // A step inside a dialog that has closed (after a reload, say) goes
        // back to the step that opens it; the completion check below still
        // runs first, so a finished action moves forward instead.
        if (++misses > 4 && step.resume && !finished && !checking) {
          finished = true; move(STEPS.findIndex((item) => item.id === step.resume)); return;
        }
        if (misses > 15) setMissing(true);
      } else {
        misses = 0; target.current = element; setMissing(false);
        const box = element.getBoundingClientRect();
        if (box.bottom < 0 || box.top > innerHeight) element.scrollIntoView({ block: "center", behavior: "instant" });
        const next = geometry(element);
        setRect((old) => (old && JSON.stringify(old) === JSON.stringify(next) ? old : next));
      }
      if (step.kind !== "do" || checking || finished) return;
      checking = true;
      try {
        if (await step.done(ctx) && alive && !finished) {
          finished = true; setCompleted(true);
          setTimeout(() => alive && move(index + 1), 900);
        }
      } catch { /* the app may still be loading */ }
      checking = false;
    };
    tick();
    const timer = setInterval(tick, 400);
    return () => { alive = false; clearInterval(timer); target.current = null; };
  }, [phase, index, state?.cell_id]);

  // Only the highlighted control (on "do" steps) and the dialogue take input.
  useEffect(() => {
    if (phase !== "step") return undefined;
    const allowed = (node) => bubble.current?.contains(node) || (step.kind === "do" && target.current?.contains(node));
    const onClick = (event) => {
      if (allowed(event.target)) return;
      event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
    };
    const onKey = (event) => {
      if (event.key === "Tab") {
        const inside = allowed(document.activeElement);
        if (!inside) { event.preventDefault(); bubble.current?.querySelector("[data-tour-primary]")?.focus(); }
        return;
      }
      if (!allowed(document.activeElement)) { event.preventDefault(); event.stopImmediatePropagation(); }
    };
    document.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKey, true);
    return () => { document.removeEventListener("click", onClick, true); document.removeEventListener("keydown", onKey, true); };
  }, [phase, index]);

  const doItForMe = async () => {
    if (busy || completed) return;
    setBusy(true); setError(null);
    try {
      if (typeof step.auto !== "function") {
        throw new Error("This step cannot be done automatically. Use the highlighted control to complete it.");
      }
      const highlighted = await waitForVisibleTarget(step.target(ctx));
      target.current = highlighted;
      await step.auto(ctx);
    } catch (problem) {
      setError(problem instanceof Error ? problem : new Error(String(problem)));
    } finally {
      setBusy(false);
    }
  };

  if (phase === "loading" || phase === "off" || !state) return null;
  if (phase === "title") return html`<${TitleCard} busy=${busy} onBegin=${begin} onSkip=${() => finish("skipped")} />`;
  if (phase === "chapter") return html`<${ChapterCard} index=${step.chapter} onStart=${() => setPhase("step")} />`;
  if (phase === "finale") return html`<${Finale} names=${state.names || {}} cellId=${state.cell_id}
    onClose=${() => { setPhase("off"); navigate("home"); }} />`;

  const shade = (x, y, w, h) => html`<div class="tour-shade" style=${`left:${x}px;top:${y}px;width:${Math.max(0, w)}px;height:${Math.max(0, h)}px`}></div>`;
  const previousIsTalk = index > 0 && STEPS[index - 1].kind === "talk" && STEPS[index - 1].chapter === step.chapter;
  return html`<div class="tour-layer" aria-label="Tutorial">
    ${rect ? html`${shade(0, 0, innerWidth, rect.y)}${shade(0, rect.y, rect.x, rect.h)}
      ${shade(rect.x + rect.w, rect.y, innerWidth - rect.x - rect.w, rect.h)}
      ${shade(0, rect.y + rect.h, innerWidth, innerHeight - rect.y - rect.h)}
      <div class=${`tour-ring ${completed ? "done" : ""}`} style=${`left:${rect.x}px;top:${rect.y}px;width:${rect.w}px;height:${rect.h}px`}></div>
      ${step.kind !== "do" && html`<div class="tour-hole-block" style=${`left:${rect.x}px;top:${rect.y}px;width:${rect.w}px;height:${rect.h}px`}></div>`}`
      : shade(0, 0, innerWidth, innerHeight)}
    <section ref=${bubble} class=${`tour-bubble ${rect?.y > innerHeight / 2 ? "at-top" : "at-bottom"}`} role="dialog" aria-label="Tutorial step">
      <div class="tour-progress" aria-label=${`Chapter ${step.chapter + 1} of ${CHAPTERS.length}`}>${CHAPTERS.map((chapter, at) => html`<span
        class=${at < step.chapter ? "past" : at === step.chapter ? "now" : ""} title=${chapter.title}></span>`)}</div>
      <div class="row between"><span class="eyebrow">Chapter ${step.chapter + 1} · ${CHAPTERS[step.chapter].title}</span>
        <button class="ghost tour-skip" disabled=${busy} onClick=${skip}>Skip tutorial</button></div>
      ${step.kind === "do" ? html`<div class=${`tour-objective ${completed ? "done" : ""}`}>${completed ? "✓ Objective complete" : "◎ Objective"}</div>` : ""}
      <h2>${step.title}</h2><p>${step.text(ctx)}</p>
      ${missing && html`<p class="notice">Can't find that on this screen.
        ${step.page ? html`<button class="ghost" onClick=${() => navigate(typeof step.page === "function" ? step.page(ctx) : step.page)}>Take me there</button>` : ""}</p>`}
      ${error && html`<p class="notice bad" role="alert">${error.message}</p>`}
      <div class="row between">
        <button disabled=${!previousIsTalk || busy} onClick=${() => move(index - 1)}>← Back</button>
        ${step.kind === "do"
          ? html`<button data-tour-primary class="ghost" disabled=${busy || completed} onClick=${doItForMe}>${busy ? "Working…" : "Do it for me"}</button>`
          : html`<button data-tour-primary class="primary" onClick=${() => (step.final ? move(STEPS.length) : move(index + 1))}>${step.final ? "Finish" : "Next →"}</button>`}
      </div>
    </section>
  </div>`;
}
