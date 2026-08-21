import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const stylesheet = readFileSync("src/styles.css", "utf8");
const buttonPrimitive = readFileSync("src/components/ui/button.tsx", "utf8");
const rootBlock = stylesheet.match(/:root\s*\{[\s\S]*?\n\}/u)?.[0] ?? "";
const pageStyles = stylesheet.replace(rootBlock, "");

describe("CSS density tokens", () => {
  it("keeps page font sizes behind root tokens", () => {
    const declarations = [...pageStyles.matchAll(/font-size:\s*([^;]+);/gu)].map(
      ([, value]) => value.trim(),
    );

    expect(declarations.length).toBeGreaterThan(0);
    expect(declarations.every((value) => value.startsWith("var(--font-"))).toBe(true);
  });

  it("keeps the Button primitive on the control typography token", () => {
    expect(buttonPrimitive).toContain("text-[length:var(--font-control)]");
    expect(buttonPrimitive).toContain("leading-5");
    expect(buttonPrimitive).not.toMatch(/\btext-(?:xs|sm|base|lg|xl)\b/u);
  });

  it("does not use negative margins for readable UI content", () => {
    expect(pageStyles).not.toMatch(/margin(?:-[a-z]+)?\s*:\s*-[^;]+;/u);
  });

  it("does not hide a numeric font size in the font shorthand", () => {
    expect(pageStyles).not.toMatch(/\bfont:\s*[^;]*(?:\d+(?:\.\d+)?)(?:px|rem)/u);
  });

  it("uses spacing tokens at the main layout seams", () => {
    for (const [contract, expectedMatches] of [
      [/\.job-list\s*\{[^}]*?gap: var\(--space-3\);/gu, 1],
      [/\.term-map-upload form\s*\{[^}]*?gap: var\(--space-4\);/gu, 1],
      [/\.term-map-layout\s*\{[^}]*?gap: var\(--space-5\);/gu, 2],
      [/\.job-list-panel > section\s*\{[^}]*?margin-top: var\(--space-5\);/gu, 1],
      [/\.job-layout\s*\{[^}]*?gap: var\(--space-5\);/gu, 1],
      [/\.workflow-panel\s*\{[^}]*?padding: var\(--space-5\) 0;/gu, 1],
      [/\.sidebar\s*\{[^}]*?padding: var\(--space-4\) var\(--space-3\);/gu, 1],
      [/\.job-detail-header\s*\{[^}]*?gap: var\(--space-2\) var\(--space-3\);/gu, 1],
      [
        /\.queue-success-summary\s*\{[^}]*?gap: var\(--space-3\) var\(--space-5\);/gu,
        1,
      ],
    ]) {
      expect([...stylesheet.matchAll(contract)]).toHaveLength(expectedMatches);
    }
  });
});
