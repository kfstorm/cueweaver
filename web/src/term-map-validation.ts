export const MAX_TERM_MAP_BYTES = 1024 * 1024;

export interface TermMapContentValidation {
  content: Record<string, string> | null;
  entryCount: number;
  byteLength: number;
  error: string | null;
}

export function validateTermMapContent(text: string): TermMapContentValidation {
  let parsed: JsonValue;
  try {
    parsed = new JsonParser(text).parse();
  } catch {
    return invalidTermMapContent('Enter valid JSON, such as {"Source":"Target"}.');
  }

  if (
    parsed === null ||
    typeof parsed !== "object" ||
    Array.isArray(parsed) ||
    !("kind" in parsed) ||
    parsed.kind !== "object"
  ) {
    return invalidTermMapContent("Term map JSON must be a non-empty object.");
  }
  if (parsed.entries.length === 0) {
    return invalidTermMapContent("Term map JSON must contain at least one mapping.");
  }

  const content: Record<string, string> = {};
  const foldedSources = new Set<string>();
  for (const [source, target] of parsed.entries) {
    if (!source) {
      return invalidTermMapContent("Source keys must be non-empty strings.");
    }
    if (typeof target !== "string" || !target) {
      return invalidTermMapContent("Target values must be non-empty strings.");
    }
    const foldedSource = casefold(source);
    if (foldedSources.has(foldedSource)) {
      return invalidTermMapContent(
        "Source keys must be unique regardless of case; remove the duplicate mapping.",
      );
    }
    foldedSources.add(foldedSource);
    Object.defineProperty(content, source, {
      configurable: true,
      enumerable: true,
      value: target,
      writable: true,
    });
  }

  const byteLength = new TextEncoder().encode(JSON.stringify(content)).byteLength;
  if (byteLength > MAX_TERM_MAP_BYTES) {
    return invalidTermMapContent("Term map must be at most 1 MiB when compacted.");
  }
  return { content, entryCount: parsed.entries.length, byteLength, error: null };
}

function invalidTermMapContent(error: string): TermMapContentValidation {
  return { content: null, entryCount: 0, byteLength: 0, error };
}

// ECMAScript lowercasing supplies the ordinary Unicode lowercase mapping.
// This is the complete Unicode 16.0 set of lowercase results whose full
// casefold differs from that lowercase result, generated from Python's data.
const CASEFOLD_EXCEPTIONS: ReadonlyMap<number, string> = new Map([
  [0x00b5, "\u03bc"],
  [0x00df, "ss"],
  [0x0149, "\u02bcn"],
  [0x017f, "s"],
  [0x01f0, "j\u030c"],
  [0x0345, "\u03b9"],
  [0x0390, "\u03b9\u0308\u0301"],
  [0x03b0, "\u03c5\u0308\u0301"],
  [0x03c2, "\u03c3"],
  [0x03d0, "\u03b2"],
  [0x03d1, "\u03b8"],
  [0x03d5, "\u03c6"],
  [0x03d6, "\u03c0"],
  [0x03f0, "\u03ba"],
  [0x03f1, "\u03c1"],
  [0x03f5, "\u03b5"],
  [0x0587, "\u0565\u0582"],
  [0xab70, "\u13a0"],
  [0xab71, "\u13a1"],
  [0xab72, "\u13a2"],
  [0xab73, "\u13a3"],
  [0xab74, "\u13a4"],
  [0xab75, "\u13a5"],
  [0xab76, "\u13a6"],
  [0xab77, "\u13a7"],
  [0xab78, "\u13a8"],
  [0xab79, "\u13a9"],
  [0xab7a, "\u13aa"],
  [0xab7b, "\u13ab"],
  [0xab7c, "\u13ac"],
  [0xab7d, "\u13ad"],
  [0xab7e, "\u13ae"],
  [0xab7f, "\u13af"],
  [0xab80, "\u13b0"],
  [0xab81, "\u13b1"],
  [0xab82, "\u13b2"],
  [0xab83, "\u13b3"],
  [0xab84, "\u13b4"],
  [0xab85, "\u13b5"],
  [0xab86, "\u13b6"],
  [0xab87, "\u13b7"],
  [0xab88, "\u13b8"],
  [0xab89, "\u13b9"],
  [0xab8a, "\u13ba"],
  [0xab8b, "\u13bb"],
  [0xab8c, "\u13bc"],
  [0xab8d, "\u13bd"],
  [0xab8e, "\u13be"],
  [0xab8f, "\u13bf"],
  [0xab90, "\u13c0"],
  [0xab91, "\u13c1"],
  [0xab92, "\u13c2"],
  [0xab93, "\u13c3"],
  [0xab94, "\u13c4"],
  [0xab95, "\u13c5"],
  [0xab96, "\u13c6"],
  [0xab97, "\u13c7"],
  [0xab98, "\u13c8"],
  [0xab99, "\u13c9"],
  [0xab9a, "\u13ca"],
  [0xab9b, "\u13cb"],
  [0xab9c, "\u13cc"],
  [0xab9d, "\u13cd"],
  [0xab9e, "\u13ce"],
  [0xab9f, "\u13cf"],
  [0xaba0, "\u13d0"],
  [0xaba1, "\u13d1"],
  [0xaba2, "\u13d2"],
  [0xaba3, "\u13d3"],
  [0xaba4, "\u13d4"],
  [0xaba5, "\u13d5"],
  [0xaba6, "\u13d6"],
  [0xaba7, "\u13d7"],
  [0xaba8, "\u13d8"],
  [0xaba9, "\u13d9"],
  [0xabaa, "\u13da"],
  [0xabab, "\u13db"],
  [0xabac, "\u13dc"],
  [0xabad, "\u13dd"],
  [0xabae, "\u13de"],
  [0xabaf, "\u13df"],
  [0xabb0, "\u13e0"],
  [0xabb1, "\u13e1"],
  [0xabb2, "\u13e2"],
  [0xabb3, "\u13e3"],
  [0xabb4, "\u13e4"],
  [0xabb5, "\u13e5"],
  [0xabb6, "\u13e6"],
  [0xabb7, "\u13e7"],
  [0xabb8, "\u13e8"],
  [0xabb9, "\u13e9"],
  [0xabba, "\u13ea"],
  [0xabbb, "\u13eb"],
  [0xabbc, "\u13ec"],
  [0xabbd, "\u13ed"],
  [0xabbe, "\u13ee"],
  [0xabbf, "\u13ef"],
  [0x13f8, "\u13f0"],
  [0x13f9, "\u13f1"],
  [0x13fa, "\u13f2"],
  [0x13fb, "\u13f3"],
  [0x13fc, "\u13f4"],
  [0x13fd, "\u13f5"],
  [0x1c80, "\u0432"],
  [0x1c81, "\u0434"],
  [0x1c82, "\u043e"],
  [0x1c83, "\u0441"],
  [0x1c84, "\u0442"],
  [0x1c85, "\u0442"],
  [0x1c86, "\u044a"],
  [0x1c87, "\u0463"],
  [0x1c88, "\ua64b"],
  [0x1e96, "h\u0331"],
  [0x1e97, "t\u0308"],
  [0x1e98, "w\u030a"],
  [0x1e99, "y\u030a"],
  [0x1e9a, "a\u02be"],
  [0x1e9b, "\u1e61"],
  [0x1f50, "\u03c5\u0313"],
  [0x1f52, "\u03c5\u0313\u0300"],
  [0x1f54, "\u03c5\u0313\u0301"],
  [0x1f56, "\u03c5\u0313\u0342"],
  [0x1f80, "\u1f00\u03b9"],
  [0x1f81, "\u1f01\u03b9"],
  [0x1f82, "\u1f02\u03b9"],
  [0x1f83, "\u1f03\u03b9"],
  [0x1f84, "\u1f04\u03b9"],
  [0x1f85, "\u1f05\u03b9"],
  [0x1f86, "\u1f06\u03b9"],
  [0x1f87, "\u1f07\u03b9"],
  [0x1f90, "\u1f20\u03b9"],
  [0x1f91, "\u1f21\u03b9"],
  [0x1f92, "\u1f22\u03b9"],
  [0x1f93, "\u1f23\u03b9"],
  [0x1f94, "\u1f24\u03b9"],
  [0x1f95, "\u1f25\u03b9"],
  [0x1f96, "\u1f26\u03b9"],
  [0x1f97, "\u1f27\u03b9"],
  [0x1fa0, "\u1f60\u03b9"],
  [0x1fa1, "\u1f61\u03b9"],
  [0x1fa2, "\u1f62\u03b9"],
  [0x1fa3, "\u1f63\u03b9"],
  [0x1fa4, "\u1f64\u03b9"],
  [0x1fa5, "\u1f65\u03b9"],
  [0x1fa6, "\u1f66\u03b9"],
  [0x1fa7, "\u1f67\u03b9"],
  [0x1fb2, "\u1f70\u03b9"],
  [0x1fb3, "\u03b1\u03b9"],
  [0x1fb4, "\u03ac\u03b9"],
  [0x1fb6, "\u03b1\u0342"],
  [0x1fb7, "\u03b1\u0342\u03b9"],
  [0x1fbe, "\u03b9"],
  [0x1fc2, "\u1f74\u03b9"],
  [0x1fc3, "\u03b7\u03b9"],
  [0x1fc4, "\u03ae\u03b9"],
  [0x1fc6, "\u03b7\u0342"],
  [0x1fc7, "\u03b7\u0342\u03b9"],
  [0x1fd2, "\u03b9\u0308\u0300"],
  [0x1fd3, "\u03b9\u0308\u0301"],
  [0x1fd6, "\u03b9\u0342"],
  [0x1fd7, "\u03b9\u0308\u0342"],
  [0x1fe2, "\u03c5\u0308\u0300"],
  [0x1fe3, "\u03c5\u0308\u0301"],
  [0x1fe4, "\u03c1\u0313"],
  [0x1fe6, "\u03c5\u0342"],
  [0x1fe7, "\u03c5\u0308\u0342"],
  [0x1ff2, "\u1f7c\u03b9"],
  [0x1ff3, "\u03c9\u03b9"],
  [0x1ff4, "\u03ce\u03b9"],
  [0x1ff6, "\u03c9\u0342"],
  [0x1ff7, "\u03c9\u0342\u03b9"],
  [0xfb00, "ff"],
  [0xfb01, "fi"],
  [0xfb02, "fl"],
  [0xfb03, "ffi"],
  [0xfb04, "ffl"],
  [0xfb05, "st"],
  [0xfb06, "st"],
  [0xfb13, "\u0574\u0576"],
  [0xfb14, "\u0574\u0565"],
  [0xfb15, "\u0574\u056b"],
  [0xfb16, "\u057e\u0576"],
  [0xfb17, "\u0574\u056d"],
]);

// Keep browser lowercasing aligned with the Python Unicode data used by the
// supported server runtime when a newer browser has added a mapping.
const PYTHON_LOWERCASE_OVERRIDES: ReadonlyMap<number, string> = new Map([
  [0xa7ce, "\ua7ce"],
  [0xa7d2, "\ua7d2"],
  [0xa7d4, "\ua7d4"],
  [0x16ea0, "\u16ea0"],
  [0x16ea1, "\u16ea1"],
  [0x16ea2, "\u16ea2"],
  [0x16ea3, "\u16ea3"],
  [0x16ea4, "\u16ea4"],
  [0x16ea5, "\u16ea5"],
  [0x16ea6, "\u16ea6"],
  [0x16ea7, "\u16ea7"],
  [0x16ea8, "\u16ea8"],
  [0x16ea9, "\u16ea9"],
  [0x16eaa, "\u16eaa"],
  [0x16eab, "\u16eab"],
  [0x16eac, "\u16eac"],
  [0x16ead, "\u16ead"],
  [0x16eae, "\u16eae"],
  [0x16eaf, "\u16eaf"],
  [0x16eb0, "\u16eb0"],
  [0x16eb1, "\u16eb1"],
  [0x16eb2, "\u16eb2"],
  [0x16eb3, "\u16eb3"],
  [0x16eb4, "\u16eb4"],
  [0x16eb5, "\u16eb5"],
  [0x16eb6, "\u16eb6"],
  [0x16eb7, "\u16eb7"],
  [0x16eb8, "\u16eb8"],
]);

function casefold(value: string): string {
  let folded = "";
  for (const character of value) {
    const codePoint = character.codePointAt(0)!;
    const lowered =
      PYTHON_LOWERCASE_OVERRIDES.get(codePoint) ?? character.toLowerCase();
    for (const loweredCharacter of lowered) {
      folded +=
        CASEFOLD_EXCEPTIONS.get(loweredCharacter.codePointAt(0)!) ?? loweredCharacter;
    }
  }
  return folded;
}

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { kind: "object"; entries: Array<[string, JsonValue]> };

class JsonParser {
  private position = 0;

  constructor(private readonly text: string) {}

  parse(): JsonValue {
    this.skipWhitespace();
    const value = this.parseValue();
    this.skipWhitespace();
    if (this.position !== this.text.length) throw new Error("Trailing JSON data");
    return value;
  }

  private parseValue(): JsonValue {
    this.skipWhitespace();
    const character = this.text[this.position];
    if (character === '"') return this.parseString();
    if (character === "{") return this.parseObject();
    if (character === "[") return this.parseArray();
    if (character === "t") return this.parseLiteral("true", true);
    if (character === "f") return this.parseLiteral("false", false);
    if (character === "n") return this.parseLiteral("null", null);
    return this.parseNumber();
  }

  private parseObject(): { kind: "object"; entries: Array<[string, JsonValue]> } {
    this.expect("{");
    const entries: Array<[string, JsonValue]> = [];
    this.skipWhitespace();
    if (this.consume("}")) return { kind: "object", entries };
    while (true) {
      this.skipWhitespace();
      const key = this.parseString();
      this.skipWhitespace();
      this.expect(":");
      entries.push([key, this.parseValue()]);
      this.skipWhitespace();
      if (this.consume("}")) return { kind: "object", entries };
      this.expect(",");
    }
  }

  private parseArray(): JsonValue[] {
    this.expect("[");
    const values: JsonValue[] = [];
    this.skipWhitespace();
    if (this.consume("]")) return values;
    while (true) {
      values.push(this.parseValue());
      this.skipWhitespace();
      if (this.consume("]")) return values;
      this.expect(",");
    }
  }

  private parseString(): string {
    const start = this.position;
    this.expect('"');
    while (this.position < this.text.length) {
      const character = this.text[this.position++];
      if (character === "\\") {
        if (this.position >= this.text.length) throw new Error("Incomplete escape");
        this.position += 1;
      } else if (character === '"') {
        const value: unknown = JSON.parse(this.text.slice(start, this.position));
        if (typeof value !== "string") throw new Error("Invalid JSON string");
        return value;
      } else if (character < " ") {
        throw new Error("Invalid control character");
      }
    }
    throw new Error("Unterminated string");
  }

  private parseNumber(): number {
    const match = this.text
      .slice(this.position)
      .match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/u);
    if (!match) throw new Error("Invalid JSON value");
    this.position += match[0].length;
    return Number(match[0]);
  }

  private parseLiteral<T extends boolean | null>(literal: string, value: T): T {
    if (this.text.slice(this.position, this.position + literal.length) !== literal) {
      throw new Error("Invalid JSON literal");
    }
    this.position += literal.length;
    return value;
  }

  private skipWhitespace() {
    while (
      this.position < this.text.length &&
      " \t\r\n".includes(this.text[this.position])
    ) {
      this.position += 1;
    }
  }

  private consume(character: string): boolean {
    if (this.text[this.position] !== character) return false;
    this.position += 1;
    return true;
  }

  private expect(character: string) {
    if (!this.consume(character)) throw new Error(`Expected ${character}`);
  }
}
