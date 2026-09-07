import { describe, expect, it } from "vitest";
import { customDayBounds, presetBounds, startOfLocalDay } from "./time";

describe("Analytics period helpers", () => {
  it("computes a UTC day as [midnight, midnight + 24h)", () => {
    const now = new Date("2026-01-15T12:34:56Z");
    const bounds = presetBounds("day", now, "UTC");
    expect(bounds).toEqual({
      from: "2026-01-15T00:00:00.000Z",
      to: "2026-01-16T00:00:00.000Z",
    });
  });

  it("computes the day in Europe/Moscow (UTC+3, no DST)", () => {
    const now = new Date("2026-01-15T12:00:00Z"); // 15:00 MSK
    const bounds = presetBounds("day", now, "Europe/Moscow");
    expect(bounds).toEqual({
      from: "2026-01-14T21:00:00.000Z", // 00:00 MSK
      to: "2026-01-15T21:00:00.000Z",
    });
  });

  it("handles the Europe/Berlin spring-forward DST day (23 hours)", () => {
    // 2026-03-29: clocks jump 02:00 CET -> 03:00 CEST.
    const now = new Date("2026-03-29T12:00:00Z"); // 14:00 CEST
    const bounds = presetBounds("day", now, "Europe/Berlin");
    expect(bounds.from).toBe("2026-03-28T23:00:00.000Z"); // 00:00 CET
    expect(bounds.to).toBe("2026-03-29T22:00:00.000Z"); // 00:00 CEST next day
    expect(new Date(bounds.to).getTime() - new Date(bounds.from).getTime()).toBe(
      23 * 60 * 60 * 1000
    );
  });

  it("computes the week as Monday..next Monday in the timezone", () => {
    // 2026-01-14 is a Wednesday (UTC and MSK share the date at noon).
    const bounds = presetBounds("week", new Date("2026-01-14T12:00:00Z"), "Europe/Moscow");
    expect(bounds.from).toBe("2026-01-11T21:00:00.000Z"); // Monday 00:00 MSK
    expect(bounds.to).toBe("2026-01-18T21:00:00.000Z");
  });

  it("computes the month in Europe/Moscow", () => {
    const bounds = presetBounds("month", new Date("2026-02-10T12:00:00Z"), "Europe/Moscow");
    expect(bounds.from).toBe("2026-01-31T21:00:00.000Z"); // Feb 1 00:00 MSK
    expect(bounds.to).toBe("2026-02-28T21:00:00.000Z"); // Mar 1 00:00 MSK
  });

  it("computes the quarter in Europe/Moscow", () => {
    const bounds = presetBounds("quarter", new Date("2026-02-10T12:00:00Z"), "Europe/Moscow");
    expect(bounds.from).toBe("2025-12-31T21:00:00.000Z"); // Jan 1 00:00 MSK
    expect(bounds.to).toBe("2026-03-31T21:00:00.000Z"); // Apr 1 00:00 MSK
  });

  it("finds local midnight for a date crossing timezones", () => {
    // 2026-01-15 02:00 MSK is still 2026-01-14 in UTC.
    const midnight = startOfLocalDay(new Date("2026-01-14T23:00:00Z"), "Europe/Moscow");
    expect(midnight.toISOString()).toBe("2026-01-14T21:00:00.000Z");
  });

  it("parses a custom date into local-midnight bounds", () => {
    const bounds = customDayBounds("2026-01-15", "Europe/Moscow");
    expect(bounds).toEqual({
      from: "2026-01-14T21:00:00.000Z",
      to: "2026-01-15T21:00:00.000Z",
    });
  });

  it("rejects malformed custom dates", () => {
    expect(customDayBounds("15.01.2026", "UTC")).toBeNull();
    expect(customDayBounds("", "UTC")).toBeNull();
  });
});
