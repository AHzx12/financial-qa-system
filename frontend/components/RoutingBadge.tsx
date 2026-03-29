"use client";

import { BarChart3, BookOpen, MessageCircle, GitCompare } from "lucide-react";


const config = {
  market_data: {
    label: "Market Data",
    icon: BarChart3,
    bg: "bg-green-500/10",
    text: "text-green-400",
    border: "border-green-500/20",
  },
  knowledge: {
    label: "Knowledge Base",
    icon: BookOpen,
    bg: "bg-blue-500/10",
    text: "text-blue-400",
    border: "border-blue-500/20",
  },
  general: {
    label: "General",
    icon: MessageCircle,
    bg: "bg-amber-500/10",
    text: "text-amber-400",
    border: "border-amber-500/20",
  },
  compound: {
    label: "Multi-Source",
    icon: GitCompare,
    bg: "bg-purple-500/10",
    text: "text-purple-400",
    border: "border-purple-500/20",
},
};

export default function RoutingBadge({
  category,
  ticker,
}: {
  category: "market_data" | "knowledge" | "general";
  ticker?: string;
}) {
const c = config[category] || config.general;
  const Icon = c.icon;
  return (
    <div
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${c.bg} ${c.text} ${c.border} animate-fade-in`}
    >
      <Icon size={12} />
      <span>{c.label}</span>
      {ticker && <span className="font-mono opacity-70">· {ticker}</span>}
    </div>
  );
}
