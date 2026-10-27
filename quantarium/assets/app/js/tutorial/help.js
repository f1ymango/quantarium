import { html, useEffect, useRef, useState } from "../ui.js";
import { chapters, topics, features, chapterForTopic } from "./content.js";

const pages = chapters.flatMap((chapter) => [
  { chapter, kind: "overview", title: chapter.title },
  { chapter, kind: "steps", title: `${chapter.title} · Try it` },
  { chapter, kind: "details", title: `${chapter.title} · Reference` },
]);

export function HelpButton({ topic = "modes", label }) {
  const initial = chapterForTopic(topic) || chapters[0];
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(Math.max(0, pages.findIndex((page) => page.chapter === initial)));
  const trigger = useRef(null);
  const panel = useRef(null);
  const page = pages[index];

  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement;
    panel.current?.querySelector("[data-help-close]")?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); setOpen(false); }
      if (event.key !== "Tab") return;
      const focusable = [...panel.current.querySelectorAll("button:not([disabled]), a, select")];
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); (trigger.current || previous)?.focus(); };
  }, [open]);

  return html`<span class="help-anchor">
    <button ref=${trigger} type="button" class="help-button" aria-label=${`Open ${label || initial.title} handbook`}
      aria-haspopup="dialog" aria-expanded=${open} title="Open handbook" onClick=${() => { setIndex(Math.max(0, pages.findIndex((item) => item.chapter === initial))); setOpen(true); }}>?</button>
    ${open && html`<div class="help-backdrop" onClick=${(event) => event.target === event.currentTarget && setOpen(false)}>
      <section ref=${panel} class="help-panel" role="dialog" aria-modal="true" aria-label="Quantarium handbook">
        <div class="row between"><div><span class="eyebrow">Quantarium handbook</span><h2>${page.title}</h2></div>
          <button data-help-close class="ghost" aria-label="Close handbook" onClick=${() => setOpen(false)}>×</button></div>
        <div class="help-progress" aria-label=${`Slide ${index + 1} of ${pages.length}`}><span style=${`width:${100 * (index + 1) / pages.length}%`}></span></div>
        <label class="field"><span>Chapter</span><select value=${page.chapter.id} onChange=${(event) => setIndex(pages.findIndex((item) => item.chapter.id === event.target.value))}>
          ${chapters.map((chapter) => html`<option value=${chapter.id}>${chapter.title}</option>`)}</select></label>
        <div class="help-slide">
          ${page.kind === "overview" && html`<p class="help-lead">${page.chapter.intro}</p>
            <p>${page.chapter.details[0]}</p>
            ${page.chapter.id === "start" && html`<figure class="help-figure"><img src="/tutorial-images/tour-spotlight.jpg" alt="The tutorial highlighting the Run dialog, with a chapter progress bar and an objective in the dialogue below" />
              <figcaption>The tutorial spotlights one control at a time and checks off each objective as you complete it.</figcaption></figure>`}
            <span class="badge ${page.chapter.kind === "guided" ? "info" : "neutral"}">${page.chapter.kind}</span>`}
          ${page.kind === "steps" && html`<p class="muted">Follow these in the guided tour or explore them at your own pace.</p>
            <ol class="learn-steps">${page.chapter.steps.map((step) => html`<li>${step}</li>`)}</ol>`}
          ${page.kind === "details" && html`${page.chapter.details.map((detail) => html`<p>${detail}</p>`)}
            ${page.chapter.topics.map((id) => html`<div class="help-topic"><strong>${topics[id][0]}</strong><p>${topics[id][1]}</p></div>`)}
            <h3>Feature availability</h3><div class="learn-features">${features.filter(([id]) => id === page.chapter.id).map(([, name, status]) =>
              html`<div><span>${name}</span><span class=${`badge ${status === "UI" ? "info" : status === "Unavailable" ? "warn" : "neutral"}`}>${status}</span></div>`)}</div>`}
        </div>
        <div class="row between help-controls"><span class="muted">${index + 1} / ${pages.length}</span><div class="row">
          <button disabled=${index === 0} onClick=${() => setIndex(index - 1)}>← Previous</button>
          <button class="primary" disabled=${index === pages.length - 1} onClick=${() => setIndex(index + 1)}>Next →</button>
        </div></div>
      </section></div>`}
  </span>`;
}
