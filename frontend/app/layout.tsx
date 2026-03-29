import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Financial QA System",
  description: "AI-powered financial asset Q&A with real-time market data and RAG",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
