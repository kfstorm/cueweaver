import { afterEach, describe, expect, it } from "vitest";

import { setActiveLocale } from "../src/i18n";
import { formatLocalTimestamp } from "../src/lib/utils";

afterEach(() => {
  setActiveLocale("en");
});

describe("timestamp formatting", () => {
  it("does not append a timezone name to local timestamps", () => {
    const timestamp = new Date("2026-08-13T12:00:00Z");
    const formatted = formatLocalTimestamp(timestamp.toISOString());
    const expected = new Intl.DateTimeFormat("en", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(timestamp);

    expect(formatted).toBe(expected);
  });
});
