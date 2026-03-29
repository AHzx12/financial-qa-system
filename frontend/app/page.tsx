"use client";

import { useState, useCallback } from "react";
import ChatWindow from "@/components/ChatWindow";
import SessionSidebar from "@/components/SessionSidebar";
import ErrorBoundary from "@/components/ErrorBoundary";

export default function Home() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  const handleSelectSession = useCallback((id: string) => {
    setSessionId(id);
  }, []);

  const handleNewChat = useCallback(() => {
    setSessionId(null);
  }, []);

  const handleSessionCreated = useCallback((id: string) => {
    setSessionId(id);
    // Trigger sidebar refresh to show the new session
    setRefreshTrigger((n) => n + 1);
  }, []);

  return (
    <main className="h-screen bg-[#0C0F14] flex">
      <SessionSidebar
        currentSessionId={sessionId}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        refreshTrigger={refreshTrigger}
      />
      <ErrorBoundary>
        <ChatWindow
          sessionId={sessionId}
          onSessionCreated={handleSessionCreated}
        />
      </ErrorBoundary>
    </main>
  );
}
