// Text size, colors, and spacing. Saved in this browser only.

const KEY = "paperwork-translator-settings";
const DEFAULTS = { textSize: "normal", theme: "auto", spacing: false };

export function loadSettings() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") };
  } catch {
    return { ...DEFAULTS };
  }
}

export function applySettings(settings) {
  const classes = document.documentElement.classList;
  classes.remove("text-large", "text-xl", "theme-light", "theme-dark", "theme-contrast", "spacing-wide");
  if (settings.textSize !== "normal") classes.add(`text-${settings.textSize}`);
  if (settings.theme !== "auto") classes.add(`theme-${settings.theme}`);
  if (settings.spacing) classes.add("spacing-wide");
}

export function setupSettings(container) {
  const settings = loadSettings();
  for (const input of container.querySelectorAll("input[name='text-size']")) {
    input.checked = input.value === settings.textSize;
  }
  for (const input of container.querySelectorAll("input[name='theme']")) {
    input.checked = input.value === settings.theme;
  }
  container.querySelector("#spacing").checked = settings.spacing;

  container.addEventListener("change", (event) => {
    const { name, value, checked } = event.target;
    if (name === "text-size") settings.textSize = value;
    else if (name === "theme") settings.theme = value;
    else if (name === "spacing") settings.spacing = checked;
    else return;
    try {
      localStorage.setItem(KEY, JSON.stringify(settings));
    } catch {
      // Private browsing can block storage; the change still applies to this visit.
    }
    applySettings(settings);
  });
}
