import { afterEach, describe, expect, it } from "vitest";

import {
  detectLocale,
  formatError,
  getLocaleTable,
  getTranslationKeys,
  getUntranslatedKeys,
  localizedError,
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
    expect(resolveLocale("zh-Hant-TW")).toBe("zh-CN");
    expect(resolveLocale("zh-HK")).toBe("zh-CN");
    expect(resolveLocale("zh-MO")).toBe("zh-CN");
    expect(resolveLocale("zh-Hant-HK")).toBe("zh-CN");
    expect(resolveLocale("pt-PT")).toBe("en");
    expect(resolveLocale("fr-CA")).toBe("en");
    expect(resolveLocale("xx-YY")).toBe("en");
  });

  it("prefers a stored locale, then the first supported browser locale", () => {
    expect(detectLocale("ja", ["zh-CN"])).toBe("zh-CN");
    expect(detectLocale("zh-CN", ["en"])).toBe("zh-CN");
    expect(detectLocale(null, ["xx-YY", "de-DE"])).toBe("en");
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
      if (locale !== "en") expect(getUntranslatedKeys(locale), locale).toEqual([]);
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

  it("formats a cached localized error with the active locale", () => {
    const error = localizedError("errors.mediaDirectory");
    setActiveLocale("en");
    expect(formatError(error)).toBe("This Media directory could not be loaded.");
    setActiveLocale("zh-CN");
    expect(formatError(error)).toBe("无法加载此 Media 目录。");
  });

  it("uses separate confirmation messages instead of English plural suffixes", () => {
    setActiveLocale("zh-CN");
    expect(translate("jobs.clearConfirmationPlural", { count: 2 })).not.toContain(
      "任务s",
    );
    expect(translate("jobs.clearConfirmationSingular", { count: 1 })).not.toContain(
      "任务s",
    );
  });
});
