import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { aiDataApi } from "@/modules/ai-data/api";
import { problemMessage } from "@/shared/api/client";
import { toast } from "@/shared/store/uiStore";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { PageHeader } from "@/shared/ui/PageHeader";

const EXAMPLES = [
  "How many open tickets do we have?",
  "Show a breakdown of tickets by status",
  "How many tickets by category?",
  "Create a ticket about printer offline on floor 3",
  "How many knowledge articles do we have?",
  "Search knowledge for VPN",
];

export function AiDataPage() {
  const [instruction, setInstruction] = useState("");

  const run = useMutation({
    mutationFn: (text: string) => aiDataApi.query(text),
    onError: (e) => toast.error(problemMessage(e)),
  });

  const submit = (text: string) => {
    const t = text.trim();
    if (t) {
      setInstruction(t);
      run.mutate(t);
    }
  };

  const data = run.data;

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="AI Data API"
        subtitle="Ask the database in plain English — the LLM chooses the operation and runs it"
      />

      <Card>
        <div className="flex items-end gap-2">
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(instruction);
              }
            }}
            rows={2}
            placeholder='e.g. "How many open tickets do we have?" or "Create a ticket about VPN not connecting"'
            className="flex-1 resize-none rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none"
          />
          <Button loading={run.isPending} onClick={() => submit(instruction)}>Run</Button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => submit(ex)}
              className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600 hover:bg-slate-100"
            >
              {ex}
            </button>
          ))}
        </div>
      </Card>

      {run.isPending && (
        <p className="mt-4 text-center text-sm text-slate-400">The LLM is choosing a data operation…</p>
      )}

      {data && !run.isPending && (
        <Card className="mt-4">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-slate-400">LLM chose operation:</span>
            <Badge tone="indigo">{data.tool}</Badge>
            <span className="text-slate-400">planner: {data.planner}</span>
          </div>
          <p className="rounded-lg bg-brand-50 px-3 py-2 text-sm text-slate-800">{data.answer}</p>
          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-slate-400">Raw data returned from the database</summary>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-900 p-3 text-[11px] text-slate-100">
{JSON.stringify(data.result, null, 2)}
            </pre>
          </details>
        </Card>
      )}
    </div>
  );
}
