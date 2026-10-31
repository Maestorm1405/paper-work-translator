// Run with: node --test tests/calendar.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildICS, googleCalendarUrl } from "../paperwork_translator/static/calendar.js";

test("all-day event ends the next day and has a reminder", () => {
  const ics = buildICS([{ title: "Send your appeal", description: "by April 18, 2027", date: "2027-04-18", time: null }]);
  assert.match(ics, /^BEGIN:VCALENDAR\r\n/);
  assert.match(ics, /DTSTART;VALUE=DATE:20270418\r\n/);
  assert.match(ics, /DTEND;VALUE=DATE:20270419\r\n/);
  assert.match(ics, /TRIGGER:-P1D\r\n/);
  assert.match(ics, /END:VCALENDAR\r\n$/);
});

test("timed event lasts one hour, and month ends roll over", () => {
  const ics = buildICS([{ title: "Visit", description: "", date: "2026-12-31", time: "23:30" }]);
  assert.match(ics, /DTSTART:20261231T233000\r\n/);
  assert.match(ics, /DTEND:20270101T003000\r\n/);
});

test("special characters are escaped and long lines are folded", () => {
  const title = "Call Dr. Patel; bring list, insurance card\\notes " + "é".repeat(60);
  const ics = buildICS([{ title, description: "line one\nline two", date: "2026-11-18", time: null }]);
  assert.ok(ics.includes("Call Dr. Patel\\; bring list\\, insurance card\\\\notes"));
  assert.ok(ics.includes("DESCRIPTION:line one\\nline two"));
  for (const raw of ics.split("\r\n")) {
    assert.ok(new TextEncoder().encode(raw).length <= 75, `line too long: ${raw}`);
  }
});

test("several events go in one file", () => {
  const ics = buildICS([
    { title: "A", date: "2026-11-14", time: null },
    { title: "B", date: "2026-11-18", time: "14:30" },
  ]);
  assert.equal(ics.match(/BEGIN:VEVENT/g).length, 2);
});

test("Google Calendar link has the right dates", () => {
  const timed = new URL(googleCalendarUrl({ title: "Urology", description: "4th floor", date: "2026-11-18", time: "14:30" }));
  assert.equal(timed.searchParams.get("action"), "TEMPLATE");
  assert.equal(timed.searchParams.get("dates"), "20261118T143000/20261118T153000");
  assert.equal(timed.searchParams.get("text"), "Urology");
  const allDay = new URL(googleCalendarUrl({ title: "Pay bill", date: "2026-11-14", time: null }));
  assert.equal(allDay.searchParams.get("dates"), "20261114/20261115");
});
