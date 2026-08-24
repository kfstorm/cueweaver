import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

import { getActiveLocale, translate, type Locale } from "../i18n";

const localTimestampFormatters = new Map<Locale, Intl.DateTimeFormat>();
const utcTimestampFormatters = new Map<Locale, Intl.DateTimeFormat>();
const relativeTimestampFormatters = new Map<Locale, Intl.RelativeTimeFormat>();

function getLocalTimestampFormatter(locale: Locale): Intl.DateTimeFormat {
  let formatter = localTimestampFormatters.get(locale);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locale, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
    localTimestampFormatters.set(locale, formatter);
  }
  return formatter;
}

function getUtcTimestampFormatter(locale: Locale): Intl.DateTimeFormat {
  let formatter = utcTimestampFormatters.get(locale);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locale, {
      dateStyle: "medium",
      timeStyle: "medium",
      timeZone: "UTC",
    });
    utcTimestampFormatters.set(locale, formatter);
  }
  return formatter;
}

function getRelativeTimestampFormatter(locale: Locale): Intl.RelativeTimeFormat {
  let formatter = relativeTimestampFormatters.get(locale);
  if (!formatter) {
    formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
    relativeTimestampFormatters.set(locale, formatter);
  }
  return formatter;
}

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

function formatTimestamp(value: string | null, format: (date: Date) => string): string {
  if (value === null) return translate("time.notRecorded");
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? translate("time.timestampUnavailable")
    : format(date);
}

export function formatLocalTimestamp(value: string | null): string {
  const locale = getActiveLocale();
  return formatTimestamp(value, (date) =>
    getLocalTimestampFormatter(locale).format(date),
  );
}

export function formatUtcTimestamp(value: string | null): string {
  const locale = getActiveLocale();
  return formatTimestamp(
    value,
    (date) =>
      `${getUtcTimestampFormatter(locale).format(date)} ${translate("time.utc")}`,
  );
}

export function formatRelativeTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return translate("time.unavailable");

  const seconds = (date.valueOf() - Date.now()) / 1000;
  const absoluteSeconds = Math.abs(seconds);
  const relativeTime = getRelativeTimestampFormatter(getActiveLocale());
  if (absoluteSeconds < 60) return relativeTime.format(Math.round(seconds), "second");
  if (absoluteSeconds < 3600)
    return relativeTime.format(Math.round(seconds / 60), "minute");
  if (absoluteSeconds < 86400)
    return relativeTime.format(Math.round(seconds / 3600), "hour");
  if (absoluteSeconds < 2592000)
    return relativeTime.format(Math.round(seconds / 86400), "day");
  if (absoluteSeconds < 31536000)
    return relativeTime.format(Math.round(seconds / 2592000), "month");
  return relativeTime.format(Math.round(seconds / 31536000), "year");
}
