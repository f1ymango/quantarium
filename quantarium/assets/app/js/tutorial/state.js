// Shared first-run tutorial state, so pages can prefill forms while it runs.
let current = null;
const listeners = new Set();

export function setTutorialState(state) {
  current = state;
  listeners.forEach((listener) => listener(state));
}
export function onTutorialState(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
export const tutorialActive = () => current?.status === "active";
export const tutorialNames = () => (tutorialActive() ? current.names : null);
export const tutorialCellId = () => (tutorialActive() ? current.cell_id : null);

// The fictional market the tutorial asks the user to generate: a rally, a
// sharp crash, and a recovery, so a trend-following cell has something to do.
export const TUTORIAL_SCENARIO = {
  description: "Your first market: a rally, a sharp crash, then a recovery. Fictional prices.",
  // Seed 21 with these segments gives about +27%, then -26% in 15 sessions,
  // then a full recovery: a shape a trend follower visibly reacts to.
  tags: "tutorial, crash", seed: 21, start_date: "2030-01-07", symbols: "AAA",
  segments: [
    { sessions: 90, drift: 45, volatility: 15 },
    { sessions: 15, drift: -600, volatility: 40 },
    { sessions: 95, drift: 50, volatility: 20 },
  ],
};
export const TUTORIAL_FEED_THROUGH = "2030-06-28";
