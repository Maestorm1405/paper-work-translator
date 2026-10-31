// Medical Paperwork Translator: add a paper, show the plain-language version, read it aloud, ask about it.

import { setupSettings } from "./settings.js";
import { speechSupported, hasVoiceFor, createReader, dictationSupported, createDictation } from "./speech.js";
import { buildICS, googleCalendarUrl, downloadFile } from "./calendar.js";
import { createScanner } from "./scanner.js";

const MAX_IMAGE_SIDE = 2576; // Claude's largest image size; bigger photos are shrunk before upload
const MAX_IMAGE_BYTES = 4.5 * 1024 * 1024;
const UI_LANG = "en";

const $ = (id) => document.getElementById(id);
const fileInput = $("file-input");
const textInput = $("text-input");
const results = $("results");
const resultsBody = $("results-body");

let languages = [];
let currentResult = null;
let lastRequest = null; // {sampleId} or {files, text}, so "Translate again" can resend it
let pages = []; // [{file, url, problem}] photos or PDFs of the paper, in page order
let maxFiles = 10;
let askHistory = [];
let sections = []; // [{title, parts: [{el, speech, lang}]}] for read-aloud

setupSettings($("settings"));

// ---------- Tabs ----------
// Add paper, then the result split into tabs: plain language, mistakes, to-dos, and questions. Settings is always there.

const tabs = [...document.querySelectorAll("[role='tab']")];
const RESULT_TABS = ["tab-explained", "tab-check", "tab-todo", "tab-ask"];

function selectTab(id, { focus = false } = {}) {
  const tab = $(id);
  if (tab.getAttribute("aria-disabled") === "true") {
    $("tabs-hint").textContent = "Add a paper first, or try a sample. Then this tab will show its results.";
    $("tabs-hint").setAttribute("role", "status");
    return;
  }
  for (const t of tabs) {
    const selected = t === tab;
    t.setAttribute("aria-selected", String(selected));
    t.tabIndex = selected ? 0 : -1;
    $(t.getAttribute("aria-controls")).hidden = !selected;
  }
  results.hidden = !RESULT_TABS.includes(id);
  if (focus) tab.focus();
}

function enableResultTabs(enabled) {
  for (const id of RESULT_TABS) {
    if (enabled) $(id).removeAttribute("aria-disabled");
    else $(id).setAttribute("aria-disabled", "true");
  }
  $("tabs-hint").hidden = enabled;
}

for (const tab of tabs) tab.addEventListener("click", () => selectTab(tab.id));

document.querySelector("[role='tablist']").addEventListener("keydown", (event) => {
  const usable = tabs.filter((t) => t.getAttribute("aria-disabled") !== "true");
  const index = usable.indexOf(document.activeElement);
  if (index < 0) return;
  const next = {
    ArrowRight: usable[(index + 1) % usable.length],
    ArrowLeft: usable[(index - 1 + usable.length) % usable.length],
    Home: usable[0],
    End: usable[usable.length - 1],
  }[event.key];
  if (!next) return;
  event.preventDefault();
  selectTab(next.id, { focus: true });
});

function badge(id, count) {
  $(id).textContent = count ? String(count) : "";
  $(id).hidden = !count;
}

// ---------- Messages ----------

const resultsShown = () => !results.hidden;

function setStatus(text) {
  $(resultsShown() ? "results-status" : "status").textContent = text;
}

function showError(message) {
  const box = $(resultsShown() ? "results-error" : "error");
  box.textContent = message;
  box.hidden = false;
}

function clearMessages() {
  for (const id of ["status", "results-status", "ask-status"]) $(id).textContent = "";
  for (const id of ["error", "results-error", "ask-error"]) $(id).hidden = true;
}

// ---------- Desktop app ----------
// In the downloadable app the page runs in its own window, and window.pywebview.api reaches the computer:
// the calendar app, file saving, links, and the saved API key. In a browser these fall back to web versions.

const desktopApi = () => window.pywebview?.api;
let desktopReady = false;

async function addToCalendar(events, filename) {
  const ics = buildICS(events);
  const api = desktopApi();
  if (api) {
    const result = await api.add_to_calendar(ics, filename).catch(() => null);
    if (result?.ok) {
      setStatus("Your calendar app opened with the event. Check the details there, then save it.");
      return;
    }
    showError(result?.error || "Couldn't open your calendar app, so the calendar file was saved instead.");
  }
  downloadFile(filename, ics, "text/calendar");
}

function openLink(url) {
  const api = desktopApi();
  if (api) api.open_url(url);
  else window.open(url, "_blank", "noopener");
}

async function refreshKeyStatus() {
  const { configured } = await desktopApi().api_key_status();
  $("key-status").textContent = configured ? "A key is saved on this computer." : "No key saved yet.";
  $("remove-key").hidden = !configured;
  $("setup-warning").hidden = configured;
}

function setupDesktop() {
  if (desktopReady || !desktopApi()) return;
  desktopReady = true;
  document.documentElement.classList.add("desktop-app");
  $("key-settings").hidden = false;
  $("setup-how").textContent =
    "To translate your own papers, open the Settings tab and paste your key under \u201cTranslation service (API key)\u201d.";
  for (const button of document.querySelectorAll(".calendar-download")) button.textContent = "Add to my calendar";
  refreshKeyStatus();
}

$("key-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = $("api-key").value.trim();
  if (!key) {
    $("key-status").textContent = "Please paste your key first.";
    $("api-key").focus();
    return;
  }
  const result = await desktopApi().save_api_key(key);
  if (!result.ok) {
    $("key-status").textContent = result.error;
    return;
  }
  $("api-key").value = "";
  await refreshKeyStatus();
  $("key-status").textContent = "Key saved. You can now translate your own papers.";
});

$("remove-key").addEventListener("click", async () => {
  await desktopApi().save_api_key("");
  await refreshKeyStatus();
  $("key-status").textContent = "Key removed. Only the sample papers work now.";
  $("api-key").focus();
});

window.addEventListener("pywebviewready", setupDesktop);
setupDesktop();

const languageName = (code) => languages.find((l) => l.code === code)?.name || code;
const isRtl = (code) => Boolean(languages.find((l) => l.code === code)?.rtl);

// ---------- Setup from the server ----------

function fillLanguageSelect(select) {
  select.replaceChildren(...languages.map((l) => {
    const option = document.createElement("option");
    option.value = l.code;
    option.textContent = l.native_name === l.name ? l.name : `${l.native_name} (${l.name})`;
    return option;
  }));
}

fetch("/api/config")
  .then((r) => r.json())
  .then((config) => {
    $("setup-warning").hidden = config.api_key_configured;
    maxFiles = config.max_files || maxFiles;
    languages = config.languages;
    for (const select of document.querySelectorAll(".language-select")) fillLanguageSelect(select);

    const list = $("sample-list");
    for (const sample of config.samples) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "sample-button";
      const title = document.createElement("strong");
      title.textContent = `Try: ${sample.title}`;
      const description = document.createElement("span");
      description.textContent = sample.description;
      button.append(title, description);
      button.addEventListener("click", () => {
        lastRequest = { sampleId: sample.id };
        runTranslation(lastRequest, $("language").value, chosenLevel("reading-level"), button);
      });
      li.append(button);
      list.append(li);
    }
    $("samples").hidden = config.samples.length === 0;
  })
  .catch(() => showError("Couldn't connect to the app. Make sure it is running, then reload this page."));

const chosenLevel = (name) => document.querySelector(`input[name='${name}']:checked`)?.value || "simple";

// ---------- Pages: photos, scans, or a PDF of the paper ----------

function renderPages() {
  const list = $("page-list");
  list.replaceChildren(...pages.map((page, i) => {
    const li = document.createElement("li");
    li.className = "page-item";
    if (page.url) {
      const img = document.createElement("img");
      img.src = page.url;
      img.alt = "";
      img.className = "page-thumb";
      li.append(img);
    } else {
      const icon = document.createElement("span");
      icon.className = "page-thumb page-thumb-pdf";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "PDF";
      li.append(icon);
    }
    const text = document.createElement("span");
    text.className = "page-text";
    const name = document.createElement("strong");
    name.textContent = page.url ? `Page ${i + 1}` : `Page ${i + 1}: ${page.file.name}`;
    text.append(name);
    if (page.problem) {
      const note = document.createElement("span");
      note.className = "page-problem";
      note.textContent = page.problem;
      text.append(note);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button-secondary";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove page ${i + 1}`);
    remove.addEventListener("click", () => removePage(i));
    li.append(text, remove);
    return li;
  }));
  $("pages").hidden = pages.length === 0;
}

function addPage(file, problem = null) {
  if (pages.length >= maxFiles) {
    showError(`You can add up to ${maxFiles} pages at a time.`);
    return pages.length;
  }
  const url = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
  pages.push({ file, url, problem });
  renderPages();
  return pages.length;
}

function removePage(index) {
  const [page] = pages.splice(index, 1);
  if (page?.url) URL.revokeObjectURL(page.url);
  renderPages();
  $("pages-status").textContent = `Page ${index + 1} removed. ${pages.length} ${pages.length === 1 ? "page" : "pages"} left.`;
  const buttons = $("page-list").querySelectorAll("button");
  (buttons[Math.min(index, buttons.length - 1)] || fileInput).focus();
}

function clearPages() {
  for (const page of pages) if (page.url) URL.revokeObjectURL(page.url);
  pages = [];
  renderPages();
}

fileInput.addEventListener("change", () => {
  for (const file of fileInput.files) addPage(file);
  const added = fileInput.files.length;
  if (added) $("pages-status").textContent = `${added} ${added === 1 ? "file" : "files"} added. ${pages.length} ${pages.length === 1 ? "page" : "pages"} in total.`;
  fileInput.value = ""; // so the same file can be picked again
});

// ---------- Camera scanner ----------

const scanner = createScanner({
  elements: {
    section: $("scanner"),
    heading: $("scanner-heading"),
    video: $("camera-view"),
    status: $("scan-status"),
    error: $("scan-error"),
    takeButton: $("take-picture"),
    torchButton: $("torch-button"),
    switchButton: $("switch-camera"),
    doneButton: $("scan-done"),
    filesButton: $("scan-use-files"),
    count: $("scan-count"),
    flash: $("camera-flash"),
  },
  onPage: (file, problem) => addPage(file, problem),
  onClose: (scanned) => {
    $("input-section").hidden = false;
    if (scanned) {
      $("pages-status").textContent = `${scanned} ${scanned === 1 ? "page" : "pages"} scanned. Check the list, then press Translate.`;
      $("pages-heading").setAttribute("tabindex", "-1");
      $("pages-heading").focus();
    } else {
      $("scan-button").focus();
    }
  },
  speak: (message) => {
    if (!$("speak-tips").checked || !speechSupported) return;
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(message);
    utterance.lang = "en-US";
    speechSynthesis.speak(utterance);
  },
});

$("scan-button").addEventListener("click", () => {
  clearMessages();
  $("input-section").hidden = true;
  scanner.open();
});

$("scan-use-files").addEventListener("click", () => {
  scanner.close();
  fileInput.click();
});

async function decodeImage(file) {
  if ("createImageBitmap" in window) return createImageBitmap(file);
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

// Shrink large photos so they upload quickly and fit the size limit. PDFs are sent as they are.
async function prepareFile(file) {
  if (!file.type.startsWith("image/")) return file;
  let image;
  try {
    image = await decodeImage(file);
  } catch {
    return file; // the browser can't open it (for example HEIC); let the server explain
  }
  const longest = Math.max(image.width, image.height);
  if (longest <= MAX_IMAGE_SIDE && file.size <= MAX_IMAGE_BYTES) return file;

  const scale = Math.min(1, MAX_IMAGE_SIDE / longest);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(image.width * scale);
  canvas.height = Math.round(image.height * scale);
  canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
  return blob ? new File([blob], "paper.jpg", { type: "image/jpeg" }) : file;
}

// ---------- Translating ----------

let busy = false;

async function runTranslation(request, language, readingLevel, button) {
  if (busy) return;
  busy = true;
  clearMessages();
  const label = button.textContent;
  button.disabled = true;
  if (button.id === "translate-button" || button.id === "rewrite-button") button.textContent = "Translating…";
  document.body.setAttribute("aria-busy", "true");
  setStatus(request.sampleId && language === "en"
    ? "Opening the sample paper."
    : "Reading your paper. This can take up to a minute.");
  const stillWorking = setTimeout(() => setStatus("Still working. Thank you for waiting."), 25000);

  try {
    const body = new FormData();
    if (request.sampleId) body.append("sample_id", request.sampleId);
    for (const file of request.files || []) body.append("files", await prepareFile(file));
    if (request.text) body.append("text", request.text);
    body.append("language", language);
    body.append("reading_level", readingLevel);
    const response = await fetch("/api/translate", { method: "POST", body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Something went wrong. Please try again.");
    setStatus("");
    showResults(data);
  } catch (err) {
    setStatus("");
    showError(err instanceof TypeError
      ? "Couldn't connect to the app. Make sure it is still running, then try again."
      : err.message);
  } finally {
    clearTimeout(stillWorking);
    busy = false;
    button.disabled = false;
    button.textContent = label;
    document.body.removeAttribute("aria-busy");
  }
}

$("translate-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = textInput.value.trim();
  if (!pages.length && !text) {
    clearMessages();
    showError("Please scan your paper, add photos or a PDF of it, or paste its words. Or try a sample paper.");
    $("scan-button").focus();
    return;
  }
  lastRequest = { files: pages.map((p) => p.file), text };
  runTranslation(lastRequest, $("language").value, chosenLevel("reading-level"), $("translate-button"));
});

$("rewrite-button").addEventListener("click", () => {
  if (!lastRequest) return;
  runTranslation(lastRequest, $("rewrite-language").value, chosenLevel("rewrite-level"), $("rewrite-button"));
});

// ---------- Showing results ----------

function sentence(text) {
  const t = (text || "").trim();
  return !t || /[.!?:。！？]$/.test(t) ? t : `${t}.`;
}

// An element holding words from the translation, marked with its language so screen readers and voices get it right.
function contentEl(tag, text, lang) {
  const el = document.createElement(tag);
  el.textContent = text;
  if (lang !== UI_LANG) {
    el.lang = lang;
    if (isRtl(lang)) el.dir = "rtl";
  }
  return el;
}

function addSection(title, items, { urgent = false, intro = null, introLang = null, container = resultsBody } = {}) {
  if (!items.length && !intro) return null;
  const lang = currentResult.language;
  const section = document.createElement("section");
  section.className = "result-section" + (urgent ? " urgent" : "");
  const headingId = `section-${sections.length}`;
  section.setAttribute("aria-labelledby", headingId);

  const header = document.createElement("div");
  header.className = "result-section-header";
  const heading = document.createElement("h3");
  heading.id = headingId;
  heading.textContent = title;
  const readButton = document.createElement("button");
  readButton.type = "button";
  readButton.className = "button-secondary read-section";
  readButton.textContent = "Read this part";
  // Starts with the visible words so voice-control users can say "click Read this part".
  readButton.setAttribute("aria-label", `Read this part: ${title}`);
  readButton.hidden = !speechSupported;
  header.append(heading, readButton);
  section.append(header);

  const entry = { title, container, parts: [{ el: heading, speech: sentence(title), lang: UI_LANG }] };

  if (intro) {
    const p = contentEl("p", intro, introLang || lang);
    section.append(p);
    entry.parts.push({ el: p, speech: intro, lang: introLang || lang });
  }

  if (items.length) {
    const list = document.createElement("ul");
    for (const item of items) {
      const itemLang = item.lang || lang;
      const li = document.createElement("li");
      if (itemLang !== UI_LANG) li.lang = itemLang;
      if (item.label) {
        const label = document.createElement("strong");
        label.className = `item-label ${item.labelClass || ""}`;
        label.lang = UI_LANG;
        label.textContent = item.label;
        li.append(label);
      }
      li.append(contentEl("span", item.text, itemLang));
      for (const detail of item.details || []) {
        const d = contentEl("span", detail, itemLang);
        d.className = "item-detail";
        li.append(d);
      }
      if (item.extra) li.append(item.extra);
      list.append(li);
      const speech = [item.label, item.text, ...(item.details || [])].map(sentence).filter(Boolean).join(" ");
      entry.parts.push({ el: li, speech, lang: itemLang });
    }
    section.append(list);
  }

  readButton.addEventListener("click", () => reader.read(entry.parts));
  sections.push(entry);
  container.append(section);
  return section;
}

const SEVERITY = {
  high: ["Check this right away", "severity-high"],
  medium: ["Check this soon", "severity-medium"],
  low: ["Small thing to check", "severity-low"],
};

const money = (n) => `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function billIntro(check) {
  if (!check) return null;
  const parts = [];
  if (check.total_charges_stated !== null) {
    parts.push(`We added up the charges on this bill: ${money(check.charges_total)}. The bill says the total is ${money(check.total_charges_stated)}.`);
  } else {
    parts.push(`We added up the charges on this bill: ${money(check.charges_total)}.`);
  }
  const allGood = check.total_matches !== false && check.amount_due_matches !== false && check.repeated_charges === 0;
  parts.push(allGood
    ? "The numbers add up. It's still a good idea to check that you got every service listed."
    : "Some things don't look right. See below.");
  return parts.join(" ");
}

function concernItems(concerns) {
  return concerns.map((c) => {
    const [label, labelClass] = SEVERITY[c.severity] || SEVERITY.medium;
    const details = [`Why it matters: ${c.why_it_matters}`, `What you can do: ${c.what_to_do}`];
    // The app's own math checks are written in English.
    if (c.source === "math_check") details.push("This was found by adding up the numbers on the bill.");
    return { label: `${label}:`, labelClass, text: c.issue, details, lang: c.source === "math_check" ? UI_LANG : null };
  });
}

function calendarEvent(todo, date, time) {
  const description = [todo.when, `From your paper: ${currentResult.document_type}`].filter(Boolean).join("\n");
  return { title: todo.task, description, date, time: time || null };
}

// "Add to calendar" opens a small panel with the date filled in when the paper gave one.
function calendarControls(todo, index) {
  const wrap = document.createElement("div");
  wrap.className = "calendar";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "button-secondary calendar-toggle";
  toggle.textContent = "Add to calendar";
  const panelId = `calendar-${index}`;
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", panelId);

  const panel = document.createElement("div");
  panel.id = panelId;
  panel.className = "calendar-panel";
  panel.hidden = true;

  const dateLabel = document.createElement("label");
  dateLabel.textContent = "Date";
  const dateInput = document.createElement("input");
  dateInput.type = "date";
  dateInput.value = todo.date || "";
  dateLabel.append(dateInput);

  const timeLabel = document.createElement("label");
  timeLabel.textContent = "Time (you can leave this empty)";
  const timeInput = document.createElement("input");
  timeInput.type = "time";
  timeInput.value = todo.time || "";
  timeLabel.append(timeInput);

  const error = document.createElement("p");
  error.className = "field-error";
  error.setAttribute("role", "alert");
  error.hidden = true;

  const buttons = document.createElement("div");
  buttons.className = "button-row";
  const download = document.createElement("button");
  download.type = "button";
  download.className = "button-primary calendar-download";
  download.textContent = desktopApi() ? "Add to my calendar" : "Download calendar file";
  const google = document.createElement("button");
  google.type = "button";
  google.className = "button-secondary";
  google.textContent = "Open in Google Calendar";
  buttons.append(download, google);

  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = desktopApi()
    ? "This opens your calendar app, like Apple Calendar or Outlook, with the event ready to save. It adds a reminder 1 day before."
    : "The calendar file works with iPhone, Android, Outlook, and most calendar apps. It adds a reminder 1 day before.";

  function event() {
    if (!dateInput.value) {
      error.textContent = "Please choose a date first.";
      error.hidden = false;
      dateInput.focus();
      return null;
    }
    error.hidden = true;
    return calendarEvent(todo, dateInput.value, timeInput.value);
  }
  download.addEventListener("click", () => {
    const e = event();
    if (e) addToCalendar([e], "appointment.ics");
  });
  google.addEventListener("click", () => {
    const e = event();
    if (e) openLink(googleCalendarUrl(e));
  });
  toggle.addEventListener("click", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) dateInput.focus();
  });

  panel.append(dateLabel, timeLabel, error, buttons, hint);
  wrap.append(toggle, panel);
  return wrap;
}

function gradeText(grade) {
  return `grade ${Math.max(1, Math.round(grade))}`;
}

function showResults(data) {
  reader.stop();
  dictation?.stop();
  currentResult = data;
  askHistory = [];
  sections = [];
  resultsBody.replaceChildren();
  $("check-body").replaceChildren();
  $("todo-body").replaceChildren();
  $("answers").replaceChildren();
  const lang = data.language;

  // Notices and reading level
  const notices = [];
  if (data.source === "sample") notices.push("This is a made-up sample paper, so you can see how the app works.");
  if (data.notice) notices.push(data.notice);
  $("result-notice").textContent = notices.join(" ");
  $("result-notice").hidden = notices.length === 0;

  const grade = data.reading_grade || {};
  const note = $("reading-level-note");
  if (grade.original && grade.plain) {
    note.textContent = `Reading level: the original paper is about ${gradeText(grade.original)}. This version is about ${gradeText(grade.plain)}.`;
  } else if (grade.plain) {
    note.textContent = `Reading level: this version is about ${gradeText(grade.plain)}.`;
  } else {
    note.textContent = "";
  }
  note.hidden = !note.textContent;

  // Sections, most important first
  const intro = data.is_medical_document
    ? [data.document_type, data.summary].filter(Boolean).map(sentence).join(" ")
    : data.summary;
  if (!data.is_medical_document) {
    $("result-notice").textContent = [$("result-notice").textContent, "This doesn't look like a medical paper or bill."].filter(Boolean).join(" ");
    $("result-notice").hidden = false;
  }
  addSection("What this paper is", [], { intro });

  addSection("Warning signs: when to get help", data.warning_signs.map((text) => ({ text })), { urgent: true });

  const hasHigh = data.concerns.some((c) => c.severity === "high");
  const check = addSection("Things to double-check", concernItems(data.concerns),
    { urgent: hasHigh, intro: billIntro(data.bill_check), introLang: UI_LANG, container: $("check-body") });
  $("check-empty").hidden = Boolean(check);
  badge("check-badge", data.concerns.length);

  addSection("The main points", data.key_points.map((text) => ({ text })));

  addSection("Things to do", data.todos.map((todo, i) => ({
    text: todo.task,
    details: todo.when ? [`When: ${todo.when}`] : [],
    extra: calendarControls(todo, i),
  })), { container: $("todo-body") });
  $("todo-empty").hidden = data.todos.length > 0;
  badge("todo-badge", data.todos.length);

  addSection("Your medicines", data.medicines.map((m) => ({
    text: `${m.name}: ${m.how_to_take}`,
    details: m.notes ? [m.notes] : [],
  })));

  addSection("Words to know", data.words_to_know.map((w) => ({ text: `${w.term}: ${w.meaning}` })));

  const questions = addSection("Questions to ask", data.questions_for_doctor.map((text) => ({ text })));
  if (questions) {
    // Printed pages get lines for writing down the answers.
    const notes = document.createElement("div");
    notes.className = "print-only notes-lines";
    notes.textContent = "Notes:";
    for (let i = 0; i < 4; i++) notes.append(document.createElement("div"));
    questions.append(notes);
  }

  addSection("Parts we couldn't read", data.unclear_parts.map((text) => ({ text })),
    { intro: data.unclear_parts.length ? "Check these parts with your doctor, pharmacist, or billing office." : null, introLang: UI_LANG });

  // Calendar for everything with a date
  const dated = data.todos.filter((t) => t.date);
  $("calendar-all").hidden = dated.length < 2;

  // Original words
  $("original-text").textContent = data.original_text || "";
  $("original-box").hidden = !data.original_text;
  $("original-box").open = false;

  // Rewrite panel starts on the current choices
  $("rewrite-language").value = lang;
  for (const input of document.querySelectorAll("input[name='rewrite-level']")) {
    input.checked = input.value === data.reading_level;
  }
  $("rewrite").open = false;

  // Suggested questions (sample papers)
  const suggestions = data.sample?.suggested_questions || [];
  $("suggested-list").replaceChildren(...suggestions.map((q) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "button-secondary suggestion";
    b.textContent = q;
    b.addEventListener("click", () => {
      $("question").value = q;
      ask();
    });
    li.append(b);
    return li;
  }));
  $("suggested").hidden = suggestions.length === 0;

  // Voice warning for languages this device may not speak
  updateVoiceWarning();

  const date = new Date().toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
  $("print-header").textContent = `Plain-language summary of: ${data.document_type}. Made on ${date} with Medical Paperwork Translator.`;

  enableResultTabs(true);
  // Mistakes come first when there are serious ones, so they aren't missed.
  selectTab(hasHigh ? "tab-check" : "tab-explained");
  $("results-heading").focus();
}

$("calendar-all").addEventListener("click", () => {
  const events = currentResult.todos.filter((t) => t.date).map((t) => calendarEvent(t, t.date, t.time));
  addToCalendar(events, "dates-from-my-paper.ics");
});

$("start-over").addEventListener("click", () => {
  reader.stop();
  dictation?.stop();
  clearMessages();
  $("translate-form").reset();
  clearPages();
  $("question").value = "";
  enableResultTabs(false);
  badge("check-badge", 0);
  badge("todo-badge", 0);
  selectTab("tab-add");
  currentResult = null;
  fileInput.focus();
});

// ---------- Print and share ----------

$("print-button").addEventListener("click", () => {
  reader.stop();
  window.print();
});

function summaryText() {
  const lines = [`${currentResult.document_type}`, "", currentResult.summary];
  for (const section of sections.slice(1)) {
    lines.push("", section.title.toUpperCase());
    for (const part of section.parts.slice(1)) lines.push(`- ${part.speech}`);
  }
  if (askHistory.length) {
    lines.push("", "QUESTIONS I ASKED");
    for (const qa of askHistory) lines.push(`- ${qa.question}`, `  ${qa.answer}`);
  }
  lines.push("", "Made with Medical Paperwork Translator. This is a helper, not medical advice. Check the original paper.");
  return lines.join("\n");
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // Older browsers, or plain http on a home network
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  const ok = document.execCommand("copy");
  area.remove();
  if (!ok) throw new Error("copy failed");
}

$("share-button").addEventListener("click", async () => {
  clearMessages();
  const text = summaryText();
  if (navigator.share) {
    try {
      await navigator.share({ title: currentResult.document_type, text });
      return;
    } catch (err) {
      if (err.name === "AbortError") return;
    }
  }
  try {
    await copyText(text);
    setStatus("Copied. You can paste it into a text message or email.");
  } catch {
    showError("Couldn't copy. Try the Print or save as PDF button instead.");
  }
});

// ---------- Read aloud ----------

const pauseButton = $("pause-reading");
const stopButton = $("stop-reading");

const reader = createReader({
  getRate: () => Number($("reading-speed").value),
  // Reading everything moves through the tabs, showing each part as it is read.
  onPart: (part) => {
    const panel = part.el.closest("[role='tabpanel']");
    if (panel?.hidden) selectTab(panel.getAttribute("aria-labelledby"));
  },
  onChange: ({ reading, paused }) => {
    pauseButton.disabled = !reading;
    stopButton.disabled = !reading;
    pauseButton.textContent = paused ? "Resume" : "Pause";
  },
});

if (!speechSupported) {
  $("read-aloud-controls").hidden = true;
  $("read-aloud-unsupported").hidden = false;
}

function updateVoiceWarning() {
  const warning = $("voice-warning");
  if (!speechSupported || !currentResult || currentResult.language === UI_LANG) {
    warning.hidden = true;
    return;
  }
  const missing = !hasVoiceFor(currentResult.language);
  warning.textContent = missing
    ? `This device may not have a voice for ${languageName(currentResult.language)}, so it may not read this well.`
    : "";
  warning.hidden = !missing;
}
if (speechSupported) speechSynthesis.addEventListener?.("voiceschanged", updateVoiceWarning);

const TAB_ORDER = ["results-body", "check-body", "todo-body"];
$("read-all").addEventListener("click", () => {
  const ordered = [...sections].sort((a, b) => TAB_ORDER.indexOf(a.container.id) - TAB_ORDER.indexOf(b.container.id));
  reader.read(ordered.flatMap((s) => s.parts));
});
stopButton.addEventListener("click", () => reader.stop());
pauseButton.addEventListener("click", () => reader.togglePause());

// ---------- Ask a question ----------

const questionBox = $("question");
const micButton = $("mic-button");

async function ask() {
  const question = questionBox.value.trim();
  $("ask-error").hidden = true;
  if (!question) {
    $("ask-error").textContent = "Please type or say a question first.";
    $("ask-error").hidden = false;
    questionBox.focus();
    return;
  }
  dictation?.stop();
  const askButton = $("ask-button");
  askButton.disabled = true;
  $("ask-status").textContent = "Finding the answer in your paper.";
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        paper_text: currentResult.original_text || "",
        translation: currentResult,
        history: askHistory,
        language: currentResult.language,
        reading_level: currentResult.reading_level,
        sample_id: currentResult.sample?.id || null,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Something went wrong. Please try again.");
    askHistory.push({ question, answer: data.answer });
    addAnswer(question, data.answer);
    questionBox.value = "";
    $("ask-status").textContent = "";
  } catch (err) {
    $("ask-status").textContent = "";
    $("ask-error").textContent = err instanceof TypeError
      ? "Couldn't connect to the app. Make sure it is still running, then try again."
      : err.message;
    $("ask-error").hidden = false;
  } finally {
    askButton.disabled = false;
  }
}

function addAnswer(question, answer) {
  const lang = currentResult.language;
  const li = document.createElement("li");
  li.className = "answer";
  const q = document.createElement("p");
  q.className = "answer-question";
  const qLabel = document.createElement("strong");
  qLabel.textContent = "You asked: ";
  q.append(qLabel, contentEl("span", question, lang));
  const a = contentEl("p", answer, lang);
  a.className = "answer-text";
  a.tabIndex = -1;
  const read = document.createElement("button");
  read.type = "button";
  read.className = "button-secondary read-section";
  read.textContent = "Read answer aloud";
  read.hidden = !speechSupported;
  read.addEventListener("click", () => reader.read([{ el: a, speech: answer, lang }]));
  li.append(q, a, read);
  $("answers").append(li);
  a.focus();
}

$("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  ask();
});

const MIC_ERRORS = {
  "not-allowed": "The microphone is blocked. Allow it in your browser settings, or type your question.",
  "service-not-allowed": "The microphone is blocked. Allow it in your browser settings, or type your question.",
  "no-speech": "We didn't hear anything. Press the button and try again.",
  "audio-capture": "We couldn't find a microphone. You can type your question instead.",
  network: "Speaking a question needs an internet connection. You can type your question instead.",
};

const dictation = dictationSupported ? createDictation({
  textarea: questionBox,
  getLang: () => currentResult?.language || UI_LANG,
  onStart: () => {
    reader.stop();
    $("ask-error").hidden = true;
    micButton.textContent = "Stop listening";
    $("ask-status").textContent = "Listening. Say your question, then press Stop listening.";
  },
  onStop: () => {
    micButton.textContent = "Speak your question";
    $("ask-status").textContent = "";
    questionBox.focus();
  },
  onError: (code) => {
    if (code === "aborted") return;
    $("ask-error").textContent = MIC_ERRORS[code] || "Speaking didn't work. You can type your question instead.";
    $("ask-error").hidden = false;
  },
}) : null;

if (dictation) {
  micButton.hidden = false;
  $("mic-hint").hidden = false;
  micButton.addEventListener("click", () => dictation.toggle());
}
