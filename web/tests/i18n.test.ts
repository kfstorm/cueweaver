import { afterEach, describe, expect, it } from "vitest";

import {
  detectLocale,
  getLocaleTable,
  getTranslationKeys,
  resolveLocale,
  setActiveLocale,
  SUPPORTED_LOCALES,
  translate,
} from "../src/i18n";

afterEach(() => {
  setActiveLocale("en");
});

describe("interface locale selection", () => {
  it("normalizes regional browser languages and falls back safely", () => {
    expect(resolveLocale("zh-Hans-CN")).toBe("zh-CN");
    expect(resolveLocale("zh-Hant-TW")).toBe("zh-TW");
    expect(resolveLocale("pt-PT")).toBe("pt-BR");
    expect(resolveLocale("fr-CA")).toBe("fr");
    expect(resolveLocale("xx-YY")).toBe("en");
  });

  it("prefers a stored locale, then the first supported browser locale", () => {
    expect(detectLocale("ja", ["zh-CN"])).toBe("ja");
    expect(detectLocale(null, ["xx-YY", "de-DE"])).toBe("de");
    expect(detectLocale(null, ["xx-YY"])).toBe("en");
  });

  it("provides translated shell labels for every supported locale", () => {
    const requiredKeys = [
      "language.label",
      "navigation.translate",
      "navigation.jobs",
      "navigation.termMaps",
      "translate.title",
      "jobs.title",
      "termMaps.title",
    ] as const;

    for (const locale of SUPPORTED_LOCALES) {
      const table = getLocaleTable(locale);
      for (const key of requiredKeys) expect(table[key]).toBeTruthy();
      for (const key of getTranslationKeys()) expect(table[key]).toBeTruthy();
    }
    expect(getTranslationKeys().length).toBeGreaterThan(100);
  });

  it("interpolates values without changing the business language code", () => {
    setActiveLocale("zh-CN");
    expect(translate("translate.selectMedia", { name: "Movie.mkv" })).toContain(
      "Movie.mkv",
    );
    expect(translate("jobs.matching", { count: 3 })).toContain("3");
    expect(translate("translate.targetLanguageCode")).toBe("目标语言代码");
  });
});
