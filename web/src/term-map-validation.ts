export const MAX_TERM_MAP_BYTES = 1024 * 1024;

export interface TermMapContentValidation {
  content: Record<string, string> | null;
  entryCount: number;
  byteLength: number;
  error: string | null;
}

export function validateTermMapContent(text: string): TermMapContentValidation {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return invalidTermMapContent('Enter valid JSON, such as {"Source":"Target"}.');
  }

  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return invalidTermMapContent("Term map JSON must be a non-empty object.");
  }
  const entries = Object.entries(parsed);
  if (entries.length === 0) {
    return invalidTermMapContent("Term map JSON must contain at least one mapping.");
  }

  let sourceKeys: string[];
  try {
    sourceKeys = readTopLevelObjectKeys(text);
  } catch {
    return invalidTermMapContent('Enter valid JSON, such as {"Source":"Target"}.');
  }

  const foldedSources = new Set<string>();
  for (const source of sourceKeys) {
    if (!source) {
      return invalidTermMapContent("Source keys must be non-empty strings.");
    }
    const foldedSource = casefold(source);
    if (foldedSources.has(foldedSource)) {
      return invalidTermMapContent(
        "Source keys must be unique regardless of case; remove the duplicate mapping.",
      );
    }
    foldedSources.add(foldedSource);
  }

  const content: Record<string, string> = {};
  for (const [source, target] of entries) {
    if (!source) {
      return invalidTermMapContent("Source keys must be non-empty strings.");
    }
    if (typeof target !== "string" || !target) {
      return invalidTermMapContent("Target values must be non-empty strings.");
    }
    Object.defineProperty(content, source, {
      configurable: true,
      enumerable: true,
      value: target,
      writable: true,
    });
  }

  const byteLength = new TextEncoder().encode(text).byteLength;
  if (byteLength > MAX_TERM_MAP_BYTES) {
    return invalidTermMapContent("Term map must be at most 1 MiB.");
  }
  return { content, entryCount: entries.length, byteLength, error: null };
}

function invalidTermMapContent(error: string): TermMapContentValidation {
  return { content: null, entryCount: 0, byteLength: 0, error };
}

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
  [0xfb00, "ff"],
  [0xfb01, "fi"],
  [0xfb02, "fl"],
  [0xfb03, "ffi"],
  [0xfb04, "ffl"],
  [0xfb05, "st"],
  [0xfb06, "st"],
]);

function casefold(value: string): string {
  let folded = "";
  for (const character of value.toLowerCase()) {
    folded += CASEFOLD_EXCEPTIONS.get(character.codePointAt(0)!) ?? character;
  }
  return folded;
}

function readTopLevelObjectKeys(text: string): string[] {
  let position = skipWhitespace(text, 0);
  if (text[position++] !== "{") throw new Error("Expected object");
  const keys: string[] = [];

  position = skipWhitespace(text, position);
  if (text[position] === "}") return keys;
  while (position < text.length) {
    const key = readString(text, position);
    keys.push(key.value);
    position = skipWhitespace(text, key.end);
    if (text[position++] !== ":") throw new Error("Expected colon");
    position = skipValue(text, skipWhitespace(text, position));
    position = skipWhitespace(text, position);
    if (text[position] === "}") return keys;
    if (text[position++] !== ",") throw new Error("Expected comma");
    position = skipWhitespace(text, position);
  }
  throw new Error("Unterminated object");
}

function readString(text: string, start: number): { value: string; end: number } {
  if (text[start] !== '"') throw new Error("Expected string");
  let position = start + 1;
  while (position < text.length) {
    const character = text[position++];
    if (character === "\\") {
      position += 1;
    } else if (character === '"') {
      const value: unknown = JSON.parse(text.slice(start, position));
      if (typeof value !== "string") throw new Error("Invalid string");
      return { value, end: position };
    }
  }
  throw new Error("Unterminated string");
}

function skipValue(text: string, start: number): number {
  const opening = text[start];
  if (opening === '"') return readString(text, start).end;
  if (opening !== "{" && opening !== "[") {
    let position = start;
    while (position < text.length && text[position] !== "," && text[position] !== "}") {
      position += 1;
    }
    return position;
  }

  const closing = opening === "{" ? "}" : "]";
  let depth = 0;
  let position = start;
  let inString = false;
  while (position < text.length) {
    const character = text[position++];
    if (inString) {
      if (character === "\\") position += 1;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') {
      inString = true;
    } else if (character === opening) {
      depth += 1;
    } else if (character === closing) {
      depth -= 1;
      if (depth === 0) return position;
    }
  }
  throw new Error("Unterminated value");
}

function skipWhitespace(text: string, start: number): number {
  let position = start;
  while (position < text.length && " \t\r\n".includes(text[position])) {
    position += 1;
  }
  return position;
}
