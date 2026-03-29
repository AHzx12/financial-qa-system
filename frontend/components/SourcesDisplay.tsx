"use client";

import { Database, Newspaper, BookOpen, ExternalLink } from "lucide-react";

interface SourcesDisplayProps {
  sources: Record<string, unknown>;
}

export default function SourcesDisplay({ sources }: SourcesDisplayProps) {
  if (!sources || Object.keys(sources).length === 0) return null;

  const marketData = sources.market_data as
    | { source: string; ticker: string; period: string; timestamp: string }
    | undefined;

  const news = sources.news as
    | Array<{ title: string; publisher: string; time: string }>
    | undefined;

  const kb = sources.knowledge_base as
    | {
        status: string;
        total_docs?: number;
        docs_used?: Array<{
          id: string;
          source: string;
          topic: string;
          relevance: number;
        }>;
      }
    | undefined;

  return (
    <div className="mt-3 pt-3 border-t border-slate-700/50 animate-fade-in">
      <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wider mb-2">
        Data sources
      </p>
      <div className="space-y-2">
        {/* Market data source */}
        {marketData && (
          <div className="flex items-start gap-2 text-xs text-slate-400">
            <Database size={12} className="mt-0.5 text-green-400 shrink-0" />
            <span>
              <span className="text-slate-300">{marketData.source}</span>
              {" · "}
              {marketData.ticker} · {marketData.period}
              {" · "}
              <span className="text-slate-500">{marketData.timestamp}</span>
            </span>
          </div>
        )}

        {/* News sources */}
        {news && news.length > 0 && (
          <div className="flex items-start gap-2 text-xs text-slate-400">
            <Newspaper size={12} className="mt-0.5 text-amber-400 shrink-0" />
            <div className="space-y-0.5">
              {news.map((n, i) => (
                <div key={i}>
                  <span className="text-slate-300">{n.title}</span>
                  <span className="text-slate-500">
                    {" "}
                    — {n.publisher} · {n.time}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Knowledge base docs */}
        {kb && (kb.docs_used ?? []).length > 0 && (
          <div className="flex items-start gap-2 text-xs text-slate-400">
            <BookOpen size={12} className="mt-0.5 text-blue-400 shrink-0" />
            <div className="space-y-0.5">
              <span className="text-slate-500">
                Knowledge base ({kb.total_docs} total docs) · Used{" "}
                {(kb.docs_used ?? []).length}:
              </span>
              {(kb.docs_used ?? []).map((doc, i) => (
                <div key={i}>
                  <span className="text-slate-300">{doc.source}</span>
                  <span className="text-slate-500">
                    {" "}
                    · {doc.topic} · relevance: {Math.max(0, doc.relevance * 100).toFixed(0)}
                    %
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
