import { type FormEvent, useState } from "react";

import { Button } from "@/shared/ui/Button";

export function Composer({
  disabled,
  streaming,
  onSend,
  onCancel,
}: {
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  };

  return (
    <form onSubmit={submit} className="flex items-end gap-2 border-t border-slate-200 bg-white p-3">
      <textarea
        className="min-h-[44px] flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-brand-500"
        placeholder="Describe your IT issue…"
        rows={1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) submit(e);
        }}
      />
      {streaming ? (
        <Button type="button" variant="danger" onClick={onCancel}>
          Stop
        </Button>
      ) : (
        <Button type="submit" disabled={disabled}>
          Send
        </Button>
      )}
    </form>
  );
}
