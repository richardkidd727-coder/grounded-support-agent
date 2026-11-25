"use client";

import { type FormEvent, type KeyboardEvent, useEffect, useRef } from "react";

const MAX_CHARS = 2000; // matches the API's ChatRequest limit

export function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
  busy,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  busy: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // grow with the content, up to a cap
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  const canSend = !disabled && !busy && value.trim().length > 0 && value.length <= MAX_CHARS;

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (canSend) onSubmit();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
  }

  return (
    <form
      onSubmit={submit}
      className="flex items-end gap-2 rounded-2xl border border-zinc-300 bg-white p-2 shadow-sm focus-within:border-sky-500 dark:border-zinc-700 dark:bg-zinc-900"
    >
      <label htmlFor="composer" className="sr-only">
        Message the support agent
      </label>
      <textarea
        id="composer"
        ref={ref}
        rows={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={disabled}
        placeholder={disabled ? "Connecting…" : "Ask about billing, refunds, sync problems…"}
        className="max-h-[200px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-zinc-400 disabled:opacity-60"
      />
      {value.length > MAX_CHARS * 0.8 && (
        <span
          className={`self-center font-mono text-[10px] ${value.length > MAX_CHARS ? "text-red-600" : "text-zinc-400"}`}
        >
          {value.length}/{MAX_CHARS}
        </span>
      )}
      <button
        type="submit"
        disabled={!canSend}
        className="rounded-xl bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {busy ? "Working…" : "Send"}
      </button>
    </form>
  );
}
