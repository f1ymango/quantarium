import { html, useState, ErrorNote, useAsync, navigate } from "../ui.js";
import { command, get } from "../api.js";
import { chapters, features } from "./content.js";
import { HelpButton } from "./help.js";
import { setTutorialState } from "./state.js";

const STATUS = {
  pending: "You haven't taken the tutorial yet.",
  active: "The tutorial is in progress. While it runs, Quantarium shows only what the tutorial made.",
  completed: "You've completed the tutorial.",
  skipped: "You skipped the tutorial.",
};

export function LearnPage({ parts }) {
  const selected = chapters.find((chapter) => chapter.id === parts[0]) || chapters[0];
  const onboarding = useAsync(() => get("onboarding"), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const replay = async () => {
    setBusy(true); setError(null);
    try { navigate("home"); setTutorialState(await command("onboarding-start")); }
    catch (problem) { setError(problem); }
    setBusy(false);
  };
  const status = onboarding.data?.status;
  return html`<div class="content learn-page">
    <div class="row between learn-heading"><div class="row"><h1>Learn Quantarium</h1><${HelpButton} topic="modes" label="Quantarium" /></div></div>
    <p class="muted" style="margin:0">Play the tutorial again, or use the handbook whenever you need a refresher.</p>
    <${ErrorNote} error=${error} />
    <div class="panel learn-hero">
      <div><span class="eyebrow">Tutorial</span><h2>Build, train, and test your first cell</h2>
        <p>Six short chapters walk you through the real app: generate a market, create a cell, train it in Static, follow it forward in Feed, find the record, and examine a controlled Lab test.
          It starts from an empty Quantarium of its own, so your other cells and records stay out of the way, and finishing adds its one new cell, market, Static training result, and Feed practice to your workspace. Lab practice stays private and is discarded.</p></div>
      <p class="hint" style="margin:0">${STATUS[status] || ""}</p>
      <div class="row learn-actions">
        <button class="primary" disabled=${busy || status === "active"} onClick=${replay}>
          ${busy ? "Starting…" : status === "pending" ? "Start the tutorial" : "Replay the tutorial"}</button>
      </div>
      <p class="hint">It never needs a data provider account. Leaving partway clears what it made. A completed replay adds one more cell, with its market, Static result, and Feed experiment, under new names; its Lab practice is discarded.</p>
    </div>
    <div class="learn-layout">
      <nav class="panel learn-contents" aria-label="Handbook chapters"><strong>Handbook chapters</strong>
        ${chapters.map((chapter) => html`<a class=${chapter === selected ? "current" : ""} href=${`#/learn/${chapter.id}`} aria-current=${chapter === selected ? "page" : undefined}>${chapter.title}</a>`)}</nav>
      <article class="panel learn-chapter"><span class="eyebrow">${selected.kind === "guided" ? "Guided path" : "Reference"}</span>
        <h2>${selected.title}</h2><p class="learn-intro">${selected.intro}</p>
        <h3>In this chapter</h3><ol class="learn-steps">${selected.steps.map((step) => html`<li>${step}</li>`)}</ol>
        <p class="muted">Use the ? beside a page title to open this handbook slide by slide. It includes explanations and the feature availability map.</p>
        <div class="learn-features">${features.filter(([id]) => id === selected.id).map(([, name, availability]) =>
          html`<div><span>${name}</span><span class=${`badge ${availability === "UI" ? "info" : availability === "Unavailable" ? "warn" : "neutral"}`}>${availability}</span></div>`)}</div>
      </article>
    </div>
  </div>`;
}
