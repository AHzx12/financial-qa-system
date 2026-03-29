"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import RoutingBadge from "./RoutingBadge";
import SourcesDisplay from "./SourcesDisplay";
import { Bot, User } from "lucide-react";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  routing?: {
    category: "market_data" | "knowledge" | "general";
    ticker?: string;
    company_name?: string;
  };
  sources?: Record<string, unknown>;
  isStreaming?: boolean;
  statusText?: string;
}

export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <div className={`animate-fade-in ${isUser ? "flex justify-end" : ""}`}>
      <div
        className={`flex gap-3 max-w-[85%] ${
          isUser ? "flex-row-reverse" : "flex-row"
        }`}
      >
        {/* Avatar */}
        <div
          className={`shrink-0 w-8 h-8 rounded-lg flex items-center justify-center mt-0.5 ${
            isUser
              ? "bg-blue-500/20 text-blue-400"
              : "bg-slate-800 text-slate-500"
          }`}
        >
          {isUser ? <User size={16} /> : <Bot size={16} />}
        </div>

        {/* Content */}
        <div className="flex flex-col gap-1.5 min-w-0">
          {!isUser && message.routing && (
            <RoutingBadge
              category={message.routing.category}
              ticker={message.routing.ticker}
            />
          )}

          <div
            className={`rounded-2xl px-4 py-3 ${
              isUser
                ? "bg-blue-600 text-white rounded-tr-sm"
                : "bg-[#131720] border border-[#1E293B] rounded-tl-sm"
            }`}
          >
            {isUser ? (
              <p className="text-sm leading-relaxed">{message.content}</p>
            ) : (
              <div className="prose text-sm text-slate-200">
                {/* Status text — shown while waiting for LLM to start streaming */}
                {message.isStreaming && message.statusText && !message.content && (
                  <p className="text-xs text-slate-400 italic mb-1">{message.statusText}</p>
                )}

                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {message.content}
                </ReactMarkdown>
                {message.isStreaming && (
                  <span className="inline-flex gap-0.5 ml-1 align-middle">
                    <span className="typing-dot w-1.5 h-1.5 rounded-full bg-blue-400 inline-block" />
                    <span className="typing-dot w-1.5 h-1.5 rounded-full bg-blue-400 inline-block" />
                    <span className="typing-dot w-1.5 h-1.5 rounded-full bg-blue-400 inline-block" />
                  </span>
                )}

                {/* Sources — shown when streaming is done */}
                {!message.isStreaming && message.sources && (
                  <SourcesDisplay sources={message.sources} />
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
