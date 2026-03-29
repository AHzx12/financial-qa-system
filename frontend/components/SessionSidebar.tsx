"use client";

import { useState, useEffect, useCallback } from "react";
import { Plus, Trash2, MessageSquare, Loader2, ChevronLeft } from "lucide-react";
import { listSessions, deleteSession } from "@/lib/api";

export interface SessionItem {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface SessionSidebarProps {
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewChat: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  refreshTrigger: number;
}

export default function SessionSidebar({
  currentSessionId,
  onSelectSession,
  onNewChat,
  collapsed,
  onToggleCollapse,
  refreshTrigger,
}: SessionSidebarProps) {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [loading, setLoading] = useState(false);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listSessions(30);
      setSessions(data);
    } catch {
      // PG might be down — silently ignore
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions, refreshTrigger]);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!confirm("Delete this conversation?")) return;
    await deleteSession(id);
    loadSessions();
    if (currentSessionId === id) {
      onNewChat();
    }
  };

  if (collapsed) {
    return (
      <div className="w-12 border-r border-[#1E293B]/50 flex flex-col items-center py-3 gap-2 shrink-0">
        <button
          onClick={onToggleCollapse}
          className="w-8 h-8 rounded-lg flex items-center justify-center text-slate-500 hover:text-slate-300 hover:bg-[#1E293B] transition-colors"
          title="Expand sidebar"
        >
          <MessageSquare size={16} />
        </button>
        <button
          onClick={onNewChat}
          className="w-8 h-8 rounded-lg flex items-center justify-center text-slate-500 hover:text-slate-300 hover:bg-[#1E293B] transition-colors"
          title="New chat"
        >
          <Plus size={16} />
        </button>
      </div>
    );
  }

  return (
    <div className="w-64 border-r border-[#1E293B]/50 flex flex-col shrink-0 bg-[#0A0D12]">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-[#1E293B]/50">
        <button
          onClick={onNewChat}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium 
            text-slate-300 bg-[#131720] border border-[#1E293B] hover:border-blue-500/30 
            transition-colors"
        >
          <Plus size={14} />
          New chat
        </button>
        <button
          onClick={onToggleCollapse}
          className="w-7 h-7 rounded-lg flex items-center justify-center text-slate-500 hover:text-slate-300 hover:bg-[#1E293B] transition-colors"
        >
          <ChevronLeft size={14} />
        </button>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto py-2">
        {loading && sessions.length === 0 ? (
          <div className="flex justify-center py-4">
            <Loader2 size={16} className="animate-spin text-slate-600" />
          </div>
        ) : sessions.length === 0 ? (
          <p className="text-xs text-slate-600 text-center py-4 px-3">
            No conversations yet
          </p>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              onClick={() => onSelectSession(s.id)}
              className={`group flex items-center gap-2 mx-2 px-2.5 py-2 rounded-lg cursor-pointer
                transition-colors text-sm truncate
                ${currentSessionId === s.id
                  ? "bg-[#1E293B] text-slate-200"
                  : "text-slate-400 hover:bg-[#131720] hover:text-slate-300"
                }`}
            >
              <MessageSquare size={14} className="shrink-0 opacity-50" />
              <span className="truncate flex-1">{s.title || "New chat"}</span>
              <button
                onClick={(e) => handleDelete(e, s.id)}
                className="hidden group-hover:flex w-5 h-5 items-center justify-center 
                  rounded text-slate-600 hover:text-red-400 shrink-0"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
