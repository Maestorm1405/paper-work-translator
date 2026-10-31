// Calendar files (.ics, for iPhone, Android, Outlook, and most calendar apps) and Google Calendar links.
// Events are {title, description, date: "YYYY-MM-DD", time: "HH:MM" or null}. Times are local to the person.

const pad = (n) => String(n).padStart(2, "0");
const compactDate = (date) => date.replaceAll("-", "");
const localStamp = (d) =>
  `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`;

function dayAfter(date) {
  const d = new Date(`${date}T00:00:00`);
  d.setDate(d.getDate() + 1);
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
}

function startAndEnd(event) {
  const start = new Date(`${event.date}T${event.time}:00`);
  const end = new Date(start.getTime() + 60 * 60 * 1000); // one hour
  return [localStamp(start), localStamp(end)];
}

function escapeText(text) {
  return text.replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
}

// Calendar lines must be at most 75 bytes; longer ones continue on the next line after a space.
function fold(line) {
  const encoder = new TextEncoder();
  let out = "";
  let length = 0;
  for (const ch of line) {
    const size = encoder.encode(ch).length;
    if (length + size > 75) {
      out += "\r\n ";
      length = 1;
    }
    out += ch;
    length += size;
  }
  return out;
}

export function buildICS(events) {
  const now = new Date();
  const stamp = `${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}T` +
    `${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}Z`;
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Medical Paperwork Translator//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"];
  events.forEach((event, i) => {
    lines.push("BEGIN:VEVENT", `UID:${stamp}-${i}-${Math.random().toString(36).slice(2)}@paperwork-translator`, `DTSTAMP:${stamp}`);
    if (event.time) {
      const [start, end] = startAndEnd(event);
      lines.push(`DTSTART:${start}`, `DTEND:${end}`);
    } else {
      lines.push(`DTSTART;VALUE=DATE:${compactDate(event.date)}`, `DTEND;VALUE=DATE:${dayAfter(event.date)}`);
    }
    lines.push(`SUMMARY:${escapeText(event.title)}`);
    if (event.description) lines.push(`DESCRIPTION:${escapeText(event.description)}`);
    lines.push("BEGIN:VALARM", "ACTION:DISPLAY", `DESCRIPTION:${escapeText(event.title)}`, "TRIGGER:-P1D", "END:VALARM", "END:VEVENT");
  });
  lines.push("END:VCALENDAR");
  return lines.map(fold).join("\r\n") + "\r\n";
}

export function googleCalendarUrl(event) {
  const dates = event.time
    ? startAndEnd(event).join("/")
    : `${compactDate(event.date)}/${dayAfter(event.date)}`;
  const params = new URLSearchParams({ action: "TEMPLATE", text: event.title, dates, details: event.description || "" });
  return `https://calendar.google.com/calendar/render?${params}`;
}

export function downloadFile(filename, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
