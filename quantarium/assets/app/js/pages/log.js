// The Log: every Static run and suite, and every Feed experiment, behind one switch.
import { html } from "../ui.js";
import { StaticList, RunDetail, SuiteMatrix } from "./static.js";
import { FeedList, FeedDetail, LiveDetail } from "./feed.js";
import { HelpButton } from "../tutorial/help.js";

const VIEWS = [["feed", "Feed"], ["static", "Static"]];

export function LogPage({ parts, query }) {
  const [kind, first, second] = parts;
  if (kind === "suite") return html`<${SuiteMatrix} config=${query.config} />`;
  if (kind === "run") return html`<${RunDetail} db=${decodeURIComponent(first)} id=${second} />`;
  if (kind === "feed") return html`<${FeedDetail} db=${decodeURIComponent(first)} id=${decodeURIComponent(second)} />`;
  if (kind === "live") return html`<${LiveDetail} db=${decodeURIComponent(first)} id=${second} />`;
  const view = query.view === "static" ? "static" : "feed";
  return html`<div class="content">
    <div class="row between">
      <div><div data-tour="log-title" class="row"><h1>Log</h1><${HelpButton} topic="log" /></div><p class="muted" style="margin:2px 0 0">Every experiment and run, newest first.</p></div>
      <div class="row" role="tablist" aria-label="Log view">${VIEWS.map(([key, label]) => html`<button data-tour=${`log-tab-${key}`} role="tab"
        aria-selected=${view === key} class=${`chip ${view === key ? "on" : ""}`} style="font-size:13px;padding:5px 14px"
        onClick=${() => (location.hash = `#/log?view=${key}`)}>${label}</button>`)}</div>
    </div>
    ${view === "feed" ? html`<${FeedList} />` : html`<${StaticList} />`}
  </div>`;
}
