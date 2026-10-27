import { html, useState, useEffect, Loading, ErrorNote, fmt } from "../ui.js";
import { get, onJobsChanged } from "../api.js";
import { HelpButton } from "../tutorial/help.js";

const TONE = { succeeded: "up", failed: "down", running: "warn", queued: "neutral" };

function seconds(job) {
  if (!job.finished_utc) return "";
  return `${((Date.parse(job.finished_utc) - Date.parse(job.submitted_utc)) / 1000).toFixed(1)} s`;
}

export function ActivityPage() {
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    let timer;
    const load = async () => {
      clearTimeout(timer);
      try {
        const list = await get("jobs");
        setJobs(list);
        if (list.some((job) => job.status === "queued" || job.status === "running")) timer = setTimeout(load, 1000);
      } catch (problem) { setError(problem); }
    };
    load();
    const stop = onJobsChanged(load);
    return () => { stop(); clearTimeout(timer); };
  }, []);
  useEffect(() => { if (open) get(`jobs/${open}`).then(setDetail, setError); else setDetail(null); }, [open, jobs]);
  return html`<div class="content">
    <div><div data-tour="activity-title" class="row"><h1>Activity</h1><${HelpButton} topic="activity" /></div><p class="muted" style="margin:2px 0 0">Jobs run one at a time. History lasts until the server stops.</p></div>
    <${ErrorNote} error=${error} />
    ${!jobs ? html`<${Loading} />` : !jobs.length ? html`<p class="muted">No jobs since the server started.</p>` : html`
      <div data-tour="activity-jobs" class="panel flush"><div class="table-wrap"><table class="data">
        <thead><tr><th>Submitted</th><th>Action</th><th>Status</th><th class="num">Took</th></tr></thead>
        <tbody>${jobs.map((job) => html`<tr class="clickable" onClick=${() => setOpen(open === job.id ? null : job.id)}>
          <td>${fmt.date(job.submitted_utc)}</td><td class="mono">${job.action}</td>
          <td><span class=${`badge ${TONE[job.status]}`}>${job.status}</span></td><td class="num">${seconds(job)}</td></tr>
          ${open === job.id && detail && html`<tr><td colspan="4">
            ${detail.error && html`<div class="notice bad">${detail.error.type}: ${detail.error.message}</div>
              <details><summary class="muted" style="cursor:pointer">Trace</summary><pre class="mono" style="white-space:pre-wrap">${detail.error.trace}</pre></details>`}
            <details open=${!detail.error}><summary class="muted" style="cursor:pointer">Parameters and result</summary>
              <pre class="mono" style="white-space:pre-wrap;max-height:320px;overflow:auto">${JSON.stringify({ params: detail.params, result: detail.result }, null, 2)}</pre></details>
          </td></tr>`}`)}</tbody></table></div></div>`}
  </div>`;
}
