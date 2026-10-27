// Client for Quantarium's local v1 API. Reads are plain GETs; writes carry the
// per-session token the server placed in the page.

const token = document.querySelector('meta[name="quantarium-token"]')?.content ?? "";
const apiBase = "/api/v1";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function parse(response) {
  const body = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) throw new ApiError(response.status, body.error || response.statusText);
  return body;
}

export async function get(route, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== ""),
  );
  const suffix = query.toString() ? `?${query}` : "";
  return parse(await fetch(`${apiBase}/${route}${suffix}`));
}

async function post(endpoint, body, base = apiBase) {
  return parse(await fetch(`${base}/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Quantarium-Token": token },
    body: JSON.stringify(body),
  }));
}

export const command = (name, params = {}) => post("commands", { command: name, params });

const listeners = new Set();
export function onJobsChanged(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// Submit a job and resolve with it once it finishes; rejects with its error.
export async function runJob(action, params = {}, onUpdate) {
  let job = await post("jobs", { action, params });
  listeners.forEach((listener) => listener());
  while (job.status === "queued" || job.status === "running") {
    onUpdate?.(job);
    await new Promise((resolve) => setTimeout(resolve, 350));
    job = await get(`jobs/${job.id}`);
  }
  listeners.forEach((listener) => listener());
  onUpdate?.(job);
  if (job.status === "failed") throw new ApiError(400, job.error?.message || "Job failed");
  return job.result;
}
