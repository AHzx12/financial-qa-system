"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Send, Loader2, Zap, Database, Brain, TrendingUp, BookOpen } from "lucide-react";
import MessageBubble, { Message } from "./MessageBubble";
import Suggestions from "./Suggestions";
import { streamChat, getSession, RoutingInfo } from "@/lib/api";

interface ChatWindowProps {
  sessionId: string | null;
  onSessionCreated: (id: string) => void;
}

export default function ChatWindow({ sessionId, onSessionCreated }: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingSession, setIsLoadingSession] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(sessionId);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isAutoCreatedRef = useRef(false);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => { scrollToBottom(); }, [messages, scrollToBottom]);

  const loadSessionMessages = useCallback(async (id: string) => {
    setIsLoadingSession(true);
    try {
      const session = await getSession(id);
      if (session?.messages) {
        setMessages(
          session.messages.map((m: { id: number; role: string; content: string; routing_category?: string; routing_ticker?: string; sources?: Record<string, unknown> }) => ({
            id: String(m.id),
            role: m.role as "user" | "assistant",
            content: m.content,
            routing: m.routing_category
              ? { category: m.routing_category as "market_data" | "knowledge" | "general" | "compound", ticker: m.routing_ticker }
              : undefined,
            sources: m.sources as Record<string, unknown> | undefined,
          }))
        );
      }
    } catch {
      setMessages([]);
    } finally {
      setIsLoadingSession(false);
    }
  }, []);

  useEffect(() => {
    setCurrentSessionId(sessionId);
    if (isAutoCreatedRef.current) {
      isAutoCreatedRef.current = false;
      return;
    }
    if (sessionId) {
      loadSessionMessages(sessionId);
    } else {
      setMessages([]);
    }
  }, [sessionId, loadSessionMessages]);

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px";
  };

  const handleSend = async (text?: string) => {
    const query = (text || input).trim();
    if (!query || isLoading) return;

    const userMsg: Message = { id: Date.now().toString(), role: "user", content: query };
    const assistantId = (Date.now() + 1).toString();
    const assistantMsg: Message = { id: assistantId, role: "assistant", content: "", isStreaming: true };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInput("");
    setIsLoading(true);
    if (inputRef.current) inputRef.current.style.height = "auto";

    const updateAssistant = (updater: (m: Message) => Partial<Message>) => {
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, ...updater(m) } : m)));
    };

    try {
      await streamChat(query, currentSessionId, {
        onRouting: (info: RoutingInfo) => {
          if (info.session_id && info.session_id !== currentSessionId) {
            setCurrentSessionId(info.session_id);
            isAutoCreatedRef.current = true;
            onSessionCreated(info.session_id);
          }
          updateAssistant(() => ({
            routing: { category: info.category, ticker: info.ticker, company_name: info.company_name },
          }));
        },
        onStatus: (status: string) => {
          updateAssistant(() => ({ statusText: status }));
        },
        onChunk: (text: string) => {
          updateAssistant((m) => ({
            content: m.content + text,
            statusText: undefined,
          }));
        },
        onSources: (sources) => {
          updateAssistant(() => ({ sources }));
        },
        onDone: () => {
          updateAssistant(() => ({ isStreaming: false }));
          setIsLoading(false);
        },
        onError: (error: string) => {
          updateAssistant((m) => ({
            content: m.content ? m.content + `\n\n⚠️ ${error}` : `⚠️ ${error}`,
            isStreaming: false,
          }));
          setIsLoading(false);
        },
      });
    } catch {
      updateAssistant(() => ({
        content: "⚠️ Connection failed. Make sure the backend is running.",
        isStreaming: false,
      }));
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const isEmpty = messages.length === 0;

  return (
    <div className="flex flex-col h-screen flex-1 min-w-0">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-[#1E293B]/50">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 to-green-500 flex items-center justify-center">
            <Zap size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-base font-semibold text-white tracking-tight">Financial QA System</h1>
            <p className="text-xs text-slate-500">Real-time data · RAG knowledge · Claude AI</p>
          </div>
        </div>
        <div className="flex items-center gap-4 text-xs text-slate-500">
          <div className="flex items-center gap-1.5"><Database size={12} /><span>Yahoo Finance</span></div>
          <div className="flex items-center gap-1.5"><Brain size={12} /><span>Claude</span></div>
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-green-500 pulse-glow" /><span>Live</span>
          </div>
        </div>
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {isEmpty ? (
          isLoadingSession ? (
            <div className="space-y-5 animate-pulse">
              {[1, 2, 3].map((i) => (
                <div key={i} className={`flex gap-3 max-w-[85%] ${i % 2 === 1 ? "" : "ml-auto flex-row-reverse"}`}>
                  <div className="w-8 h-8 rounded-lg bg-slate-800 shrink-0" />
                  <div className="flex-1 space-y-2">
                    <div className={`h-4 bg-slate-800 rounded ${i % 2 === 0 ? "w-1/3" : "w-2/3"}`} />
                    <div className={`h-4 bg-slate-800 rounded ${i % 2 === 0 ? "w-1/4" : "w-1/2"}`} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
          <div className="flex flex-col items-center justify-center h-full gap-8">
            <div className="text-center space-y-3">
              <div className="w-16 h-16 mx-auto rounded-2xl bg-gradient-to-br from-blue-500/20 to-green-500/20 border border-[#1E293B] flex items-center justify-center">
                <Zap size={28} className="text-blue-400" />
              </div>
              <h2 className="text-xl font-semibold text-white">Financial Asset QA</h2>
              <p className="text-sm text-slate-500 max-w-md">
                Ask about stock prices, market trends, or financial concepts.
              </p>
            </div>
            <div className="grid grid-cols-3 gap-3 max-w-lg w-full">
              {[
                { icon: TrendingUp, title: "Market Data", desc: "Prices, trends, news", color: "text-green-400" },
                { icon: BookOpen, title: "Knowledge RAG", desc: "20 docs, vector search", color: "text-blue-400" },
                { icon: Brain, title: "AI Analysis", desc: "Structured, data-driven", color: "text-amber-400" },
              ].map((f, i) => (
                <div key={i} className="flex flex-col items-center gap-2 p-4 rounded-xl bg-[#131720]/50 border border-[#1E293B]/50 text-center">
                  <f.icon size={20} className={f.color} />
                  <p className="text-xs font-medium text-slate-300">{f.title}</p>
                  <p className="text-[11px] text-slate-500">{f.desc}</p>
                </div>
              ))}
            </div>
            <Suggestions onSelect={handleSend} />
          </div>
          )
        ) : (
          <div className="space-y-5">
            {messages.map((msg) => <MessageBubble key={msg.id} message={msg} />)}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="px-6 pb-5 pt-2">
        {!isEmpty && !isLoading && messages.length < 12 && (
          <div className="mb-3"><Suggestions onSelect={handleSend} /></div>
        )}
        <div className="relative flex items-end gap-2 bg-[#131720] border border-[#1E293B] rounded-2xl p-2 focus-within:border-blue-500/40 transition-colors">
          <textarea
            ref={inputRef} value={input} onChange={handleInputChange} onKeyDown={handleKeyDown}
            placeholder="Ask about stocks, trends, or financial concepts..."
            rows={1}
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-500 resize-none outline-none px-2 py-1.5 max-h-[120px]"
            disabled={isLoading}
          />
          <button
            onClick={() => handleSend()}
            disabled={!input.trim() || isLoading}
            className="shrink-0 w-9 h-9 rounded-xl bg-blue-600 text-white flex items-center justify-center
              disabled:opacity-30 disabled:cursor-not-allowed hover:bg-blue-500 transition-all active:scale-95"
          >
            {isLoading ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
          </button>
        </div>
        <p className="text-center text-[11px] text-slate-600 mt-2">
          Yahoo Finance · RAG · Claude AI · Sources shown per response
        </p>
      </div>
    </div>
  );
}