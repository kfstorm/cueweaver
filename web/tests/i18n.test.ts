import { afterEach, describe, expect, it } from "vitest";

import {
  detectLocale,
  formatError,
  getLocaleTable,
  getTranslationKeys,
  getUntranslatedKeys,
  localizedError,
  LOCALE_OPTIONS,
  resolveLocale,
  setActiveLocale,
  SUPPORTED_LOCALES,
  translate,
} from "../src/i18n";

function placeholders(value: string): string[] {
  return [
    ...new Set([...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1])),
  ].sort();
}

afterEach(() => {
  setActiveLocale("en");
});

describe("interface locale selection", () => {
  it("supports the nine interface locales", () => {
    expect(SUPPORTED_LOCALES).toEqual([
      "en",
      "zh-CN",
      "zh-TW",
      "ja",
      "ko",
      "es",
      "fr",
      "de",
      "pt-BR",
    ]);
    expect(LOCALE_OPTIONS).toEqual([
      { code: "en", label: "English" },
      { code: "zh-CN", label: "简体中文" },
      { code: "zh-TW", label: "繁體中文" },
      { code: "ja", label: "日本語" },
      { code: "ko", label: "한국어" },
      { code: "es", label: "Español" },
      { code: "fr", label: "Français" },
      { code: "de", label: "Deutsch" },
      { code: "pt-BR", label: "Português (Brasil)" },
    ]);
  });

  it("normalizes regional browser languages and falls back safely", () => {
    expect(resolveLocale("zh")).toBe("zh-CN");
    expect(resolveLocale("zh-Hans-CN")).toBe("zh-CN");
    expect(resolveLocale("zh-Hant-TW")).toBe("zh-TW");
    expect(resolveLocale("zh-HK")).toBe("zh-TW");
    expect(resolveLocale("zh-MO")).toBe("zh-TW");
    expect(resolveLocale("zh-Hant-HK")).toBe("zh-TW");
    expect(resolveLocale("ja-JP")).toBe("ja");
    expect(resolveLocale("pt-BR")).toBe("pt-BR");
    expect(resolveLocale("fr-CA")).toBe("fr");
    expect(resolveLocale("de-DE")).toBe("de");
    expect(resolveLocale("es-MX")).toBe("es");
    expect(resolveLocale("ko-KR")).toBe("ko");
    expect(resolveLocale("pt-PT")).toBe("en");
    expect(resolveLocale("xx-YY")).toBe("en");
  });

  it("prefers a stored locale, then the first supported browser locale", () => {
    expect(detectLocale("ja", ["zh-CN"])).toBe("ja");
    expect(detectLocale("de-DE", ["zh-CN"])).toBe("de");
    expect(detectLocale("zh-CN", ["en"])).toBe("zh-CN");
    expect(detectLocale(null, ["xx-YY", "de-DE"])).toBe("de");
    expect(detectLocale("pt-PT", ["fr-CA"])).toBe("fr");
    expect(detectLocale(null, ["xx-YY"])).toBe("en");
  });

  it("provides translated values for every key in every non-English locale", () => {
    for (const locale of SUPPORTED_LOCALES) {
      const table = getLocaleTable(locale);
      for (const key of getTranslationKeys()) expect(table[key]).toBeTruthy();
      if (locale !== "en") expect(getUntranslatedKeys(locale), locale).toEqual([]);
    }
    expect(getTranslationKeys().length).toBeGreaterThan(100);
  });

  it("preserves the English placeholder set in every locale", () => {
    const english = getLocaleTable("en");
    for (const locale of SUPPORTED_LOCALES) {
      const table = getLocaleTable(locale);
      for (const key of getTranslationKeys()) {
        expect(placeholders(table[key]), `${locale}.${key}`).toEqual(
          placeholders(english[key]),
        );
      }
    }
  });

  it("translates the destructive Term map guidance", () => {
    expect(getLocaleTable("zh-CN")["termMaps.replaceHelp"]).toContain(
      "删除当前所有映射",
    );
    expect(getLocaleTable("zh-CN")["termMaps.deleteHelp"]).toContain("永久删除术语表");
    for (const locale of SUPPORTED_LOCALES.filter((value) => value !== "en")) {
      const table = getLocaleTable(locale);
      expect(table["termMaps.replaceHelp"], locale).not.toBe(
        getLocaleTable("en")["termMaps.replaceHelp"],
      );
      expect(table["termMaps.deleteHelp"], locale).not.toBe(
        getLocaleTable("en")["termMaps.deleteHelp"],
      );
    }
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
