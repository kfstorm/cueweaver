import { readFileSync } from "node:fs";

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

const localeSpecificTermMapExamples: Record<Locale, string> = {
  en: '{\n  "Nueva York": "New York",\n  "La capitana": "The Captain"\n}',
  "zh-CN": '{\n  "New York": "纽约",\n  "The Captain": "队长"\n}',
  "zh-TW": '{\n  "New York": "紐約",\n  "The Captain": "隊長"\n}',
  ja: '{\n  "New York": "ニューヨーク",\n  "The Captain": "隊長"\n}',
  ko: '{\n  "New York": "뉴욕",\n  "The Captain": "선장"\n}',
  es: '{\n  "New York": "Nueva York",\n  "The Captain": "La capitana"\n}',
  fr: '{\n  "New York": "New York",\n  "The Captain": "Le capitaine"\n}',
  de: '{\n  "New York": "New York",\n  "The Captain": "Der Kapitän"\n}',
  "pt-BR": '{\n  "New York": "Nova York",\n  "The Captain": "A capitã"\n}',
};

const appSource = readFileSync("src/app.tsx", "utf8");

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
    expect(getTranslationKeys().length).toBeGreaterThan(100);
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

  it("does not add ASCII spaces adjacent to CJK-script text", () => {
    const unspacedLocales: Locale[] = ["zh-CN", "zh-TW", "ja"];
    const cjkScript = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u;
    const violations: string[] = [];
    for (const locale of unspacedLocales) {
      for (const [key, value] of Object.entries(getLocaleTable(locale))) {
        for (let index = 0; index < value.length; index += 1) {
          if (
            value[index] === " " &&
            (cjkScript.test(value[index - 1] ?? "") ||
              cjkScript.test(value[index + 1] ?? ""))
          ) {
            violations.push(`${locale}.${key}: ${value}`);
            break;
          }
        }
      }
    }
    expect(violations).toEqual([]);
  });

  it("provides localized term-map guidance with distinct source and target languages", () => {
    for (const locale of SUPPORTED_LOCALES) {
      const table = getLocaleTable(locale) as Record<string, string>;
      expect(table["termMaps.nameHelp"], `${locale}.termMaps.nameHelp`).toBeTruthy();
      expect(table["termMaps.exampleJson"], `${locale}.termMaps.exampleJson`).toBe(
        localeSpecificTermMapExamples[locale],
      );
      expect(
        table["termMaps.jsonPlaceholder"],
        `${locale}.termMaps.jsonPlaceholder`,
      ).toBe(localeSpecificTermMapExamples[locale]);
      const entries = Object.entries(JSON.parse(table["termMaps.exampleJson"]));
      expect(entries.length, locale).toBeGreaterThan(0);
      expect(
        entries.some(([source, target]) => source !== target),
        locale,
      ).toBe(true);
    }
  });

  it("keeps user-visible app copy behind the translation boundary", () => {
    expect(appSource).not.toContain("<subtitle suffix>");
    expect(appSource).not.toContain("A name that helps you recognize where to use it.");
    expect(appSource).not.toContain("Term map replaced with");
    expect(appSource).not.toContain('"Source": "Target"');
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
