import type { TranslationKey } from "./index";

export class LocalizedError extends Error {
  constructor(
    readonly translationKey: TranslationKey,
    readonly detail?: string,
  ) {
    super(detail ?? translationKey);
    this.name = "LocalizedError";
  }
}

export function localizedError(key: TranslationKey, detail?: string): LocalizedError {
  return new LocalizedError(key, detail);
}
