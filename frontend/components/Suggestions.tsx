"use client";

import { TrendingUp, BookOpen, HelpCircle, GitCompareArrows } from "lucide-react";

const suggestions = [
  {
    icon: TrendingUp,
    label: "阿里巴巴最近走势",
    query: "阿里巴巴最近7天涨跌情况如何？",
    color: "text-green-400",
  },
  {
    icon: TrendingUp,
    label: "TSLA stock price",
    query: "What is Tesla's current stock price and recent trend?",
    color: "text-green-400",
  },
  {
    icon: BookOpen,
    label: "什么是市盈率",
    query: "什么是市盈率？怎么用它来评估股票？",
    color: "text-blue-400",
  },
  {
    icon: BookOpen,
    label: "收入与净利润的区别",
    query: "收入和净利润的区别是什么？",
    color: "text-blue-400",
  },
  {
    icon: GitCompareArrows,
    label: "比较苹果和微软",
    query: "比较苹果和微软的股价表现和估值",
    color: "text-purple-400",
  },
  {
    icon: HelpCircle,
    label: "如何阅读财报",
    query: "如何阅读和分析一家公司的季度财报？",
    color: "text-amber-400",
  },
];

export default function Suggestions({
  onSelect,
}: {
  onSelect: (text: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2 justify-center">
      {suggestions.map((s, i) => {
        const Icon = s.icon;
        return (
          <button
            key={i}
            onClick={() => onSelect(s.query)}
            className="flex items-center gap-2 px-3.5 py-2 rounded-xl border border-[#1E293B]
              bg-[#131720]/50 text-sm transition-all duration-200
              hover:border-blue-500/30 hover:bg-blue-500/5 active:scale-[0.97]"
          >
            <Icon size={14} className={s.color} />
            <span className="text-slate-300">{s.label}</span>
          </button>
        );
      })}
    </div>
  );
}