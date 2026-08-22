import { useMutation, useQuery } from "@tanstack/react-query";
import { type ChangeEvent, useRef, useState } from "react";

import { type DocHit, docsearchApi } from "@/modules/docsearch/api";
import { problemMessage } from "@/shared/api/client";
import { queryClient } from "@/shared/api/queryClient";
import { toast } from "@/shared/store/uiStore";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { Card } from "@/shared/ui/Card";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Spinner } from "@/shared/ui/Spinner";

const refreshDocs = () => queryClient.invalidateQueries({ queryKey: ["docsearch", "docs"] });

function HitModal({ hit, onClose }: { hit: DocHit; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <div>
            <div className="text-sm font-semibold text-slate-900">{hit.filename}</div>
            <div className="mt-0.5 text-xs text-slate-400">{hit.location} · exact text from the file</div>
          </div>
          <button onClick={onClose} className="rounded-lg px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button>
        </div>
        <div className="overflow-y-auto px-5 py-4">
          <pre className="whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-3 text-xs text-slate-800">{hit.text}</pre>
        </div>
      </div>
    </div>
  );
}

export function DocumentSearchPage() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState<{ file: File; sheets: string[] } | null>(null);
  const [chosenSheet, setChosenSheet] = useState("");
  const [url, setUrl] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<DocHit | null>(null);

  const docs = useQuery({ queryKey: ["docsearch", "docs"], queryFn: docsearchApi.list });

  const uploadMut = useMutation({
    mutationFn: ({ file, sheet }: { file: File; sheet?: string }) => docsearchApi.upload(file, sheet),
    onSuccess: (d) => { toast.success(`Indexed "${d.filename}" (${d.chunk_count} passages).`); setPending(null); refreshDocs(); },
    onError: (e) => toast.error(problemMessage(e)),
  });

  const inspectMut = useMutation({
    mutationFn: (file: File) => docsearchApi.inspect(file),
    onSuccess: (res, file) => {
      if (res.sheets && res.sheets.length > 1) {
        setPending({ file, sheets: res.sheets });
        setChosenSheet(res.sheets[0]);
      } else {
        uploadMut.mutate({ file, sheet: res.sheets?.[0] });
      }
    },
    onError: (e) => toast.error(problemMessage(e)),
  });

  const urlMut = useMutation({
    mutationFn: (u: string) => docsearchApi.addUrl(u),
    onSuccess: (d) => { toast.success(`Indexed "${d.filename}".`); setUrl(""); refreshDocs(); },
    onError: (e) => toast.error(problemMessage(e)),
  });

  const removeMut = useMutation({
    mutationFn: (id: string) => docsearchApi.remove(id),
    onSuccess: () => refreshDocs(),
    onError: (e) => toast.error(problemMessage(e)),
  });

  const searchMut = useMutation({
    mutationFn: (q: string) => docsearchApi.search(q),
    onError: (e) => toast.error(problemMessage(e)),
  });

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (fileRef.current) fileRef.current.value = "";
    if (file) inspectMut.mutate(file);
  };

  const busyUpload = inspectMut.isPending || uploadMut.isPending;

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Document Search"
        subtitle="Attach files or a URL, then search — the AI finds the spot and summarizes it"
      />

      {/* Attach */}
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.xlsm" hidden onChange={onFile} />
          <Button loading={busyUpload} onClick={() => fileRef.current?.click()}>Attach file</Button>
          <span className="text-xs text-slate-400">PDF, Word, text, or Excel</span>
          <span className="mx-2 text-slate-300">|</span>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://a-web-page…"
            className="min-w-[220px] flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none"
          />
          <Button variant="secondary" loading={urlMut.isPending} disabled={!url.trim()} onClick={() => urlMut.mutate(url.trim())}>
            Add URL
          </Button>
        </div>

        {/* Excel tab picker */}
        {pending && (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
            <span className="text-xs font-medium text-amber-800">
              "{pending.file.name}" has multiple tabs — which one should I index?
            </span>
            <select
              value={chosenSheet}
              onChange={(e) => setChosenSheet(e.target.value)}
              className="rounded-lg border border-amber-300 bg-white px-2 py-1 text-sm"
            >
              {pending.sheets.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <Button size="sm" loading={uploadMut.isPending} onClick={() => uploadMut.mutate({ file: pending.file, sheet: chosenSheet })}>
              Index this tab
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setPending(null)}>Cancel</Button>
          </div>
        )}

        {/* Attached sources */}
        <div className="mt-4">
          <div className="mb-1 text-xs font-medium text-slate-500">Attached sources</div>
          {docs.isLoading ? (
            <Spinner label="Loading…" />
          ) : !docs.data || docs.data.length === 0 ? (
            <p className="text-xs text-slate-400">Nothing attached yet.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {docs.data.map((d) => (
                <li key={d.id} className="flex items-center justify-between py-2 text-sm">
                  <span className="flex items-center gap-2">
                    <Badge>{d.source_type}</Badge>
                    <span className="text-slate-700">{d.filename}</span>
                    {d.sheet && <span className="text-xs text-slate-400">· tab: {d.sheet}</span>}
                    <span className="text-xs text-slate-400">· {d.chunk_count} passages</span>
                  </span>
                  <button onClick={() => removeMut.mutate(d.id)} className="text-slate-400 hover:text-red-500" title="Remove">✕</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>

      {/* Search */}
      <Card className="mt-4">
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") searchMut.mutate(query.trim()); }}
            placeholder="Search a keyword across your attached files…"
            className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none"
          />
          <Button loading={searchMut.isPending} disabled={!query.trim()} onClick={() => searchMut.mutate(query.trim())}>Search</Button>
        </div>

        {searchMut.data && (
          <div className="mt-4 space-y-2">
            {searchMut.data.hits.length === 0 ? (
              <EmptyState title="No matches" hint="Try a different keyword, or attach more files." />
            ) : (
              searchMut.data.hits.map((hit) => (
                <button
                  key={hit.chunk_id}
                  onClick={() => setSelected(hit)}
                  className="block w-full rounded-lg border border-slate-200 p-3.5 text-left hover:border-brand-300 hover:bg-slate-50"
                >
                  <p className="text-sm leading-relaxed text-slate-800">{hit.summary}</p>
                  <div className="mt-2 flex items-center gap-2 border-t border-slate-100 pt-2 text-xs text-slate-400">
                    <Badge>{hit.source_type}</Badge>
                    <span className="font-medium text-brand-600">📄 {hit.filename}</span>
                    <span>· {hit.location}</span>
                    <span className="ml-auto text-brand-500">view source ↗</span>
                  </div>
                </button>
              ))
            )}
          </div>
        )}
      </Card>

      {selected && <HitModal hit={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
