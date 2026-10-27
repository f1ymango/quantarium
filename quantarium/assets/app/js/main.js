import { render } from "../vendor/htm/standalone.module.js";
import { html, useEffect, useState, Icon } from "./ui.js";
import { get, onJobsChanged } from "./api.js";
import { LearnPage } from "./tutorial/learn.js";
import { GameTour } from "./tutorial/tour.js";
import { onTutorialState } from "./tutorial/state.js";
import { HomePage } from "./pages/home.js";
import { CellPage } from "./pages/cell.js";
import { TrainingPage } from "./pages/training.js";
import { ArenaPage } from "./pages/arena.js";
import { LogPage } from "./pages/log.js";
import { ScenariosPage } from "./pages/scenarios.js";
import { ActivityPage } from "./pages/activity.js";
import { WorkspacePage } from "./pages/workspace.js";
import { AgentsPage } from "./pages/agents.js";
import { InboxPage } from "./pages/inbox.js";
import { LabPage } from "./pages/lab.js";
import { AssistantPanel } from "./assistant.js";

const NAV = [
  { path: "home", label: "Home", icon: "cells" },
  { path: "feed", label: "Feed", icon: "feed" },
  { path: "scenarios", label: "Scenario", icon: "scenarios" },
  { path: "static", label: "Static", icon: "static" },
  { path: "lab", label: "Lab", icon: "lab" },
  { path: "log", label: "Log", icon: "log" },
  { path: "activity", label: "Activity", icon: "activity" },
  { path: "workspace", label: "Workspace", icon: "workspace" },
  { path: "agents", label: "Agents", icon: "agents" },
  { path: "inbox", label: "Inbox", icon: "inbox" },
  { path: "learn", label: "Learn", icon: "learn" },
];

function remembered(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage may be unavailable */ }
}

// "#/cells/c_123?tab=configure" -> { parts: ["cells", "c_123"], query: {tab: "configure"} }
function parseHash() {
  const [path, query = ""] = (location.hash.replace(/^#\/?/, "") || "home").split("?");
  return { parts: path.split("/").filter(Boolean), query: Object.fromEntries(new URLSearchParams(query)) };
}

function JobIndicator() {
  const [running, setRunning] = useState(0);
  useEffect(() => {
    let timer;
    const check = async () => {
      clearTimeout(timer);
      try {
        const jobs = await get("jobs");
        const active = jobs.filter((job) => job.status === "queued" || job.status === "running").length;
        setRunning(active);
        if (active) timer = setTimeout(check, 1000);
      } catch { /* server may be restarting */ }
    };
    check();
    const stop = onJobsChanged(check);
    return () => { stop(); clearTimeout(timer); };
  }, []);
  if (!running) return null;
  return html`<a class="job-pill" href="#/activity">${running} job${running > 1 ? "s" : ""} running</a>`;
}

function App() {
  const [route, setRoute] = useState(parseHash());
  const [collapsed, setCollapsed] = useState(remembered("quantarium.sidebar", "open") === "collapsed");
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useState(remembered("quantarium.theme", "system"));
  // The tutorial is a state with its own workspace; pages remount when it
  // starts or ends so they show the right one.
  const [scope, setScope] = useState("workspace");
  // Proposals waiting for the user, shown as a badge on Inbox.
  const [waiting, setWaiting] = useState(0);
  useEffect(() => {
    let live = true;
    const check = () => get("agents/proposals", { status: "pending" })
      .then((items) => live && setWaiting(items.length), () => {});
    check();
    const timer = setInterval(check, 15000);
    window.addEventListener("quantarium-proposals-changed", check);
    return () => { live = false; clearInterval(timer); window.removeEventListener("quantarium-proposals-changed", check); };
  }, [route]);
  useEffect(() => onTutorialState((state) => setScope(state?.status === "active" ? "tutorial" : "workspace")), []);

  useEffect(() => {
    const onHash = () => { setRoute(parseHash()); setMenuOpen(false); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    const open = () => setMenuOpen(true);
    const close = () => setMenuOpen(false);
    window.addEventListener("quantarium-tour-open-menu", open);
    window.addEventListener("quantarium-tour-close-menu", close);
    return () => { window.removeEventListener("quantarium-tour-open-menu", open); window.removeEventListener("quantarium-tour-close-menu", close); };
  }, []);
  useEffect(() => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    remember("quantarium.theme", theme);
  }, [theme]);

  const toggleCollapsed = () => {
    remember("quantarium.sidebar", collapsed ? "open" : "collapsed");
    setCollapsed(!collapsed);
  };
  const nextTheme = { system: "light", light: "dark", dark: "system" }[theme];
  const [section, ...rest] = route.parts;
  const props = { parts: rest, query: route.query };
  // A cell's own page belongs to Home in the sidebar.
  const current = section === "cells" ? "home" : section;
  const page = {
    home: html`<${HomePage} ...${props} />`,
    cells: rest.length ? html`<${CellPage} id=${rest[0]} ...${props} />` : html`<${HomePage} ...${props} />`,
    feed: html`<${ArenaPage} ...${props} />`,
    static: html`<${TrainingPage} ...${props} />`,
    lab: html`<${LabPage} ...${props} />`,
    log: html`<${LogPage} ...${props} />`,
    scenarios: html`<${ScenariosPage} ...${props} />`,
    activity: html`<${ActivityPage} ...${props} />`,
    workspace: html`<${WorkspacePage} ...${props} />`,
    agents: html`<${AgentsPage} ...${props} />`,
    inbox: html`<${InboxPage} ...${props} />`,
    learn: html`<${LearnPage} ...${props} />`,
  }[section] || html`<div class="content"><p>Page not found. <a href="#/home">Go home</a></p></div>`;

  return html`<div class=${`shell ${collapsed ? "collapsed" : ""} ${menuOpen ? "open" : ""}`}>
    <nav class="sidebar" aria-label="Main">
      <div class="brand"><span class="brand-mark" aria-hidden="true">Q</span><span class="brand-name">Quantarium</span></div>
      ${NAV.map((item) => html`<a data-tour-nav=${item.path} class=${`nav-item ${current === item.path ? "active" : ""}`} href=${`#/${item.path}`}
          title=${collapsed ? item.label : undefined} aria-current=${current === item.path ? "page" : undefined}>
          <span class="nav-icon"><${Icon} name=${item.icon} /></span><span class="nav-label">${item.label}</span>
          ${item.path === "inbox" && waiting > 0 && html`<span class="nav-badge" aria-label=${`${waiting} waiting`}>${waiting}</span>`}</a>`)}
      <div class="sidebar-foot">
        <button class="ghost nav-item" onClick=${() => setTheme(nextTheme)} title=${`Theme: ${theme}`}>
          <span class="nav-icon"><${Icon} name="theme" /></span><span class="nav-label">Theme: ${theme}</span></button>
        <button class="ghost nav-item collapse-toggle" onClick=${toggleCollapsed}
          aria-label=${collapsed ? "Expand sidebar" : "Collapse sidebar"} title=${collapsed ? "Expand sidebar" : undefined}>
          <span class="nav-icon"><${Icon} name=${collapsed ? "expand" : "collapse"} /></span><span class="nav-label">Collapse</span></button>
      </div>
    </nav>
    <div class="main" onClick=${(event) => menuOpen && !event.target.closest(".menu-button") && setMenuOpen(false)}>
      <div class="topbar">
        <button class="ghost menu-button" aria-label="Open menu" onClick=${() => setMenuOpen(true)}><${Icon} name="menu" /></button>
        <span class="faint">Quantarium</span><span class="spacer"></span>
        ${scope === "tutorial" && html`<span class="tutorial-badge" title="Only what the tutorial made is shown until it ends">Tutorial</span>`}
        <${JobIndicator} key=${scope} />
      </div>
      <div class="page-scope" key=${scope}>${page}</div>
    </div>
    ${scope !== "tutorial" && html`<${AssistantPanel} />`}
    <${GameTour} />
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
