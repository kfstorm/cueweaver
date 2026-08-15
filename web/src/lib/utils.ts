import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const localDateTime = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

const utcDateTime = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "medium",
  timeZone: "UTC",
});

const relativeTime = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function formatLocalTimestamp(value: string | null): string {
  if (value === null) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Timestamp unavailable"
    : localDateTime.format(date);
}

export function formatUtcTimestamp(value: string | null): string {
  if (value === null) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Timestamp unavailable"
    : `${utcDateTime.format(date)} UTC`;
}

export function formatRelativeTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Time unavailable";

  const seconds = (date.valueOf() - Date.now()) / 1000;
  const absoluteSeconds = Math.abs(seconds);
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
