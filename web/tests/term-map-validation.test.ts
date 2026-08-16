import contract from "../../contracts/term-map-validation.json";
import { describe, expect, it } from "vitest";
import { UNICODE_CASEFOLD_VERSION } from "../src/unicode-casefold-data";
import {
  MAX_TERM_MAP_BYTES,
  MAX_TERM_MAP_UPLOAD_BYTES,
  validateTermMapContent,
} from "../src/term-map-validation";

interface ContractCase {
  name: string;
  text?: string;
  valid: boolean;
  canonicalValid?: boolean;
  rawValid?: boolean;
  generated?: {
    targetLength: number;
    rawPadding: number;
  };
}

function caseText(testCase: ContractCase): string {
  if (testCase.generated === undefined) return testCase.text!;
  return `{"source":"${"x".repeat(testCase.generated.targetLength)}"${" ".repeat(
    testCase.generated.rawPadding,
  )}}`;
}

describe("Term map validation contract", () => {
  it("uses the shared Unicode casefold data version", () => {
    expect(UNICODE_CASEFOLD_VERSION).toBe(contract.unicodeCasefoldVersion);
  });

  for (const testCase of contract.cases as ContractCase[]) {
    it(`matches the shared vector: ${testCase.name}`, () => {
      const result = validateTermMapContent(caseText(testCase));
      expect(result.error === null).toBe(testCase.rawValid ?? testCase.valid);
      if (testCase.generated !== undefined) {
        expect(
          validateTermMapContent(
            caseText({
              ...testCase,
              generated: { ...testCase.generated, rawPadding: 0 },
            }),
          ).error === null,
        ).toBe(testCase.canonicalValid ?? testCase.valid);
      }
    });
  }

  it("reports canonical and raw UTF-8 sizes separately", () => {
    const result = validateTermMapContent('{\n  "Source": "Target"\n}');

    expect(result.error).toBeNull();
    expect(result.byteLength).toBe(
      new TextEncoder().encode('{"Source":"Target"}').byteLength,
    );
    expect(result.rawByteLength).toBe(
      new TextEncoder().encode('{\n  "Source": "Target"\n}').byteLength,
    );
    expect(result.byteLength).toBeLessThanOrEqual(MAX_TERM_MAP_BYTES);
    expect(result.rawByteLength).toBeLessThanOrEqual(MAX_TERM_MAP_UPLOAD_BYTES);
  });
});
