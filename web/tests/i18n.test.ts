import { afterEach, describe, expect, it } from "vitest";

import {
  detectLocale,
  getErrorDetail,
  formatError,
  getLocaleTable,
  getTranslationKeys,
  getMissingTranslationKeys,
  localizedError,
  LOCALE_OPTIONS,
  resolveLocale,
  setActiveLocale,
  SUPPORTED_LOCALES,
  translate,
  type Locale,
} from "../src/i18n";

function placeholders(value: string): string[] {
  return [
    ...new Set([...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1])),
  ].sort();
}

const forbiddenUntranslatedDomainTerms = [
  "Media",
  "Work",
  "Job",
  "Jobs",
  "Source",
  "Target",
  "Directory",
  "Subtitle",
  "Subtitles",
  "Provider",
  "Output",
  "Language",
  "Status",
  "Translation",
].join("|");
const forbiddenUntranslatedDomainTerm = new RegExp(
  `\\b(?:${forbiddenUntranslatedDomainTerms})\\b`,
  "u",
);
const forbiddenUntranslatedTermMap = /\bTerm maps?\b/iu;

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
    expect(LOCALE_OPTIONS.map(({ code }) => code)).toEqual(SUPPORTED_LOCALES);
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

  it("provides a non-empty value for every key in every locale", () => {
    for (const locale of SUPPORTED_LOCALES) {
      const table = getLocaleTable(locale);
      for (const key of getTranslationKeys()) expect(table[key]).toBeTruthy();
      expect(getMissingTranslationKeys(locale), locale).toEqual([]);
    }
  });

  it("uses approachable labels for job time information", () => {
    const labels: Record<Locale, string> = {
      en: "Time information",
      "zh-CN": "时间信息",
      "zh-TW": "時間資訊",
      ja: "時間情報",
      ko: "시간 정보",
      es: "Información de fecha y hora",
      fr: "Informations sur la date et l'heure",
      de: "Zeitangaben",
      "pt-BR": "Informações de data e hora",
    };

    for (const locale of SUPPORTED_LOCALES) {
      expect(getLocaleTable(locale)["jobs.timeInformation"]).toBe(labels[locale]);
    }
  });

  it("does not leave English domain terms in non-English locales", () => {
    const violations: string[] = [];
    for (const locale of SUPPORTED_LOCALES) {
      if (locale === "en") continue;
      for (const [key, value] of Object.entries(getLocaleTable(locale))) {
        if (
          forbiddenUntranslatedDomainTerm.test(value) ||
          forbiddenUntranslatedTermMap.test(value)
        ) {
          violations.push(`${locale}.${key}: ${value}`);
        }
      }
    }
    expect(violations).toEqual([]);
  });

  it("provides localized term-map guidance with distinct source and target languages", () => {
    for (const locale of SUPPORTED_LOCALES) {
      const table = getLocaleTable(locale) as Record<string, string>;
      const entries = Object.entries(JSON.parse(table["termMaps.exampleJson"]));
      expect(entries.length, locale).toBeGreaterThan(0);
      expect(
        entries.some(([source, target]) => source !== target),
        locale,
      ).toBe(true);
      expect(() => JSON.parse(table["termMaps.jsonPlaceholder"]), locale).not.toThrow();
    }
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
    expect(formatError(error)).toBe("无法加载此媒体目录。");
  });

  it("keeps server error details separate from the localized user message", () => {
    const error = localizedError("errors.mediaDirectory", "backend failure");
    setActiveLocale("zh-CN");
    expect(formatError(error)).toBe("无法加载此媒体目录。");
    expect(getErrorDetail(error)).toBe("backend failure");
  });

  it("uses i18next plural forms for confirmation messages", () => {
    setActiveLocale("zh-CN");
    expect(translate("jobs.clearConfirmation", { count: 2 })).toContain("2");
    expect(translate("jobs.clearConfirmation", { count: 1 })).toContain("1");
  });

  it("selects plural forms with i18next", () => {
    expect(translate("jobs.job", { count: 1 }, "fr")).toBe("Tâche");
    expect(translate("jobs.job", { count: 2 }, "fr")).toBe("Tâches");
    expect(translate("jobs.job", { count: 1 }, "ja")).toBe("ジョブ");
  });
});
