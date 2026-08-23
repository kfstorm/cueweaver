import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

import { getActiveLocale, translate } from "../i18n";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatLocalTimestamp(value: string | null): string {
  if (value === null) return translate("time.notRecorded");
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? translate("time.timestampUnavailable")
    : new Intl.DateTimeFormat(getActiveLocale(), {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(date);
}

export function formatUtcTimestamp(value: string | null): string {
  if (value === null) return translate("time.notRecorded");
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? translate("time.timestampUnavailable")
    : `${new Intl.DateTimeFormat(getActiveLocale(), {
        dateStyle: "medium",
        timeStyle: "medium",
        timeZone: "UTC",
      }).format(date)} ${translate("time.utc")}`;
}

export function formatRelativeTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return translate("time.unavailable");

  const seconds = (date.valueOf() - Date.now()) / 1000;
  const absoluteSeconds = Math.abs(seconds);
  const relativeTime = new Intl.RelativeTimeFormat(getActiveLocale(), {
    numeric: "auto",
  });
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
