"use strict";

const faqItems = [...document.querySelectorAll("#faq-list details")];
const personaCopy = {
  operator: {
    label: "OPERATIONS",
    headline: "One shipment, from creation to close.",
    copy: "Create the record, assign a driver, follow agreed handoffs, and share only the customer view you choose.",
  },
  driver: {
    label: "DRIVER",
    headline: "Share a location when you choose.",
    copy: "Use a dedicated page, grant browser permission, and stop sharing when you are done. Freshness is shown honestly.",
  },
  customer: {
    label: "CUSTOMER",
    headline: "Know what is happening with your delivery.",
    copy: "Open a private shipment link to see safe progress updates and a recent location when one is available.",
  },
};
const walkthroughSteps = [
  {
    label: "01 · OPERATIONS",
    title: "Create a shared shipment record",
    copy: "Record pickup and drop-off details, assign an available driver, and set the next handoff in motion.",
    location: "SAVED COORDINATES",
    progress: 18,
    longitude: 155,
    latitude: 99,
  },
  {
    label: "02 · DRIVER",
    title: "Share location by choice",
    copy: "The driver opens a separate page and grants location permission. Fresh fixes are linked to the in-transit shipment.",
    location: "OPT-IN GPS FIX",
    progress: 62,
    longitude: 245,
    latitude: 78,
  },
  {
    label: "03 · CUSTOMER",
    title: "Share a focused delivery update",
    copy: "The customer receives a read-only link for this shipment, with safe status and a recent location when available.",
    location: "CUSTOMER VIEW",
    progress: 100,
    longitude: 330,
    latitude: 58,
  },
];

let activePersona = "operator";
let activeFilter = "all";
let query = "";
let activeWalkthroughStep = 0;
let walkthroughTimer = null;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function updateFaqs() {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  let visibleCount = 0;
  let eligibleCount = 0;
  for (const item of faqItems) {
    const matchesPersona = item.dataset.personas.split(" ").includes(activePersona);
    const matchesFilter = activeFilter === "all" || item.dataset.category === activeFilter;
    if (matchesPersona && matchesFilter) eligibleCount += 1;
    const searchable = `${item.innerText} ${item.dataset.keywords}`.toLocaleLowerCase();
    const matchesQuery = !normalizedQuery || searchable.includes(normalizedQuery);
    const visible = matchesPersona && matchesFilter && matchesQuery;
    item.hidden = !visible;
    if (!visible) item.open = false;
    if (visible) visibleCount += 1;
  }

  const word = visibleCount === 1 ? "answer" : "answers";
  document.getElementById("faq-results").textContent = visibleCount
    ? `Showing ${visibleCount} of ${eligibleCount} ${word} for ${personaCopy[activePersona].label.toLocaleLowerCase()}`
    : "No answers match those choices";
  const allForPersona = faqItems.filter((item) => (
    item.dataset.personas.split(" ").includes(activePersona)
  )).length;
  document.getElementById("faq-all-count").textContent = String(allForPersona).padStart(2, "0");
  document.getElementById("faq-empty").hidden = visibleCount > 0;
}

for (const button of document.querySelectorAll(".persona-choice")) {
  button.addEventListener("click", () => {
    activePersona = button.dataset.persona;
    for (const choice of document.querySelectorAll(".persona-choice")) {
      const selected = choice === button;
      choice.classList.toggle("selected", selected);
      choice.setAttribute("aria-pressed", String(selected));
    }
    const content = personaCopy[activePersona];
    document.getElementById("persona-label").textContent = content.label;
    document.getElementById("persona-headline").textContent = content.headline;
    document.getElementById("persona-copy").textContent = content.copy;

    const currentStep = { operator: 1, driver: 2, customer: 3 }[activePersona];
    document.querySelectorAll(".journey-node").forEach((node, index) => {
      const step = index + 1;
      node.classList.toggle("complete", step < currentStep);
      node.classList.toggle("current", step === currentStep);
    });
    updateFaqs();
  });
}

function showWalkthroughStep(stepIndex) {
  activeWalkthroughStep = Math.max(0, Math.min(stepIndex, walkthroughSteps.length - 1));
  const step = walkthroughSteps[activeWalkthroughStep];
  document.getElementById("walkthrough-position").textContent = `0${activeWalkthroughStep + 1} / 03`;
  document.getElementById("film-step-label").textContent = step.label;
  document.getElementById("film-step-title").textContent = step.title;
  document.getElementById("film-step-copy").textContent = step.copy;
  document.getElementById("film-location-pill").textContent = step.location;
  document.getElementById("walkthrough-progress").style.width = `${step.progress}%`;
  document.getElementById("film-live-dot").setAttribute("cx", String(step.longitude));
  document.getElementById("film-live-dot").setAttribute("cy", String(step.latitude));
  document.getElementById("film-live-halo").setAttribute("cx", String(step.longitude));
  document.getElementById("film-live-halo").setAttribute("cy", String(step.latitude));
  document.querySelectorAll(".film-tab").forEach((tab, index) => {
    const selected = index === activeWalkthroughStep;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-pressed", String(selected));
  });
}

function stopWalkthrough() {
  if (walkthroughTimer !== null) {
    window.clearInterval(walkthroughTimer);
    walkthroughTimer = null;
  }
  const button = document.getElementById("walkthrough-play");
  button.textContent = reducedMotion ? "→" : "▶";
  button.setAttribute("aria-label", reducedMotion ? "Show next walkthrough step" : "Play walkthrough");
}

for (const tab of document.querySelectorAll(".film-tab")) {
  tab.addEventListener("click", () => {
    stopWalkthrough();
    showWalkthroughStep(Number(tab.dataset.walkthroughStep));
  });
}

document.getElementById("walkthrough-play").addEventListener("click", () => {
  if (reducedMotion) {
    showWalkthroughStep((activeWalkthroughStep + 1) % walkthroughSteps.length);
    return;
  }
  if (walkthroughTimer !== null) {
    stopWalkthrough();
    return;
  }
  if (activeWalkthroughStep === walkthroughSteps.length - 1) showWalkthroughStep(0);
  const button = document.getElementById("walkthrough-play");
  button.textContent = "Ⅱ";
  button.setAttribute("aria-label", "Pause walkthrough");
  walkthroughTimer = window.setInterval(() => {
    if (activeWalkthroughStep >= walkthroughSteps.length - 1) {
      stopWalkthrough();
      return;
    }
    showWalkthroughStep(activeWalkthroughStep + 1);
    if (activeWalkthroughStep === walkthroughSteps.length - 1) stopWalkthrough();
  }, 2600);
});

if (reducedMotion) {
  const button = document.getElementById("walkthrough-play");
  button.textContent = "→";
  button.setAttribute("aria-label", "Show next walkthrough step");
}

showWalkthroughStep(0);

for (const button of document.querySelectorAll(".faq-filter")) {
  button.addEventListener("click", () => {
    activeFilter = button.dataset.filter;
    for (const filter of document.querySelectorAll(".faq-filter")) {
      const selected = filter === button;
      filter.classList.toggle("active", selected);
      filter.setAttribute("aria-pressed", String(selected));
    }
    updateFaqs();
  });
}

document.getElementById("faq-search").addEventListener("input", (event) => {
  query = event.currentTarget.value;
  updateFaqs();
});

document.getElementById("faq-clear").addEventListener("click", () => {
  query = "";
  activeFilter = "all";
  document.getElementById("faq-search").value = "";
  for (const filter of document.querySelectorAll(".faq-filter")) {
    const selected = filter.dataset.filter === "all";
    filter.classList.toggle("active", selected);
    filter.setAttribute("aria-pressed", String(selected));
  }
  updateFaqs();
});

for (const item of faqItems) {
  item.addEventListener("toggle", () => {
    if (!item.open) return;
    for (const other of faqItems) {
      if (other !== item) other.open = false;
    }
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    document.getElementById("faq-search").focus();
  }
});

updateFaqs();
