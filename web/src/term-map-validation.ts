import { UNICODE_CASEFOLD_DATA } from "./unicode-casefold-data";

export const MAX_TERM_MAP_BYTES = 1024 * 1024;
export const MAX_TERM_MAP_UPLOAD_BYTES = MAX_TERM_MAP_BYTES;

export interface TermMapContentValidation {
  content: Record<string, string> | null;
  entryCount: number;
  rawByteLength: number;
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
    if (hasUnpairedSurrogate(source)) {
      return invalidTermMapContent("Term map must contain valid Unicode strings.");
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
    if (hasUnpairedSurrogate(target)) {
      return invalidTermMapContent("Term map must contain valid Unicode strings.");
    }
    Object.defineProperty(content, source, {
      configurable: true,
      enumerable: true,
      value: target,
      writable: true,
    });
  }

  const rawByteLength = new TextEncoder().encode(text).byteLength;
  if (rawByteLength > MAX_TERM_MAP_UPLOAD_BYTES) {
    return invalidTermMapContent("Term map must be at most 1 MiB.");
  }
  const byteLength = new TextEncoder().encode(JSON.stringify(content)).byteLength;
  if (byteLength > MAX_TERM_MAP_BYTES) {
    return invalidTermMapContent("Term map must be at most 1 MiB.");
  }
  return {
    content,
    entryCount: entries.length,
    rawByteLength,
    byteLength,
    error: null,
  };
}

function invalidTermMapContent(error: string): TermMapContentValidation {
  return { content: null, entryCount: 0, rawByteLength: 0, byteLength: 0, error };
}

function casefold(value: string): string {
  let folded = "";
  for (const character of value.toLowerCase()) {
    const codePoint = character.codePointAt(0)!;
    const mapping = UNICODE_CASEFOLD_DATA[codePoint.toString(16).toUpperCase()];
    folded += mapping === undefined ? character : foldCodePoints(mapping);
  }
  return folded;
}

function foldCodePoints(mapping: number | readonly number[]): string {
  return typeof mapping === "number"
    ? String.fromCodePoint(mapping)
    : String.fromCodePoint(...mapping);
}

function hasUnpairedSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        index += 1;
        continue;
      }
      return true;
    }
    if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) return true;
  }
  return false;
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
