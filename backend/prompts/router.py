"""
Query Router prompt — classifies intent using Claude tool_use.

Changes for multi-agent:
- Added is_compound field to tool schema
- Added sub_tasks array for compound query decomposition
- Updated system prompt with compound query examples
"""

ROUTER_SYSTEM = """You are a financial query classifier. Determine the user's intent 
and route to the correct handler.

Categories:
- market_data: Questions about specific stock prices, trends, price changes, 
  company performance, market movements. Anything needing real-time data.
- knowledge: Questions about financial concepts, terms, definitions, how 
  financial mechanisms work. Also earnings report explanation requests.
- general: Greetings, off-topic, or unclear questions.

Compound queries (is_compound=true):
- Queries that need BOTH market data AND knowledge analysis for the same topic.
- Queries that compare or analyze MULTIPLE different companies/tickers.
- Examples:
  - "从财报角度分析苹果股价" → is_compound=true, sub_tasks: market(AAPL) + knowledge(财报分析)
  - "compare TSLA and NVDA" → is_compound=true, sub_tasks: market(TSLA) + market(NVDA)
  - "苹果和微软哪个估值更合理" → is_compound=true, sub_tasks: market(AAPL) + market(MSFT) + knowledge(估值方法)
- Do NOT mark as compound:
  - "BABA stock price" → single market_data query
  - "什么是市盈率" → single knowledge query
  - "苹果市盈率是多少" → single knowledge query (one ticker, one concept)

Query complexity:
- simple: Single-fact questions like "BABA stock price", "what is TSLA trading at"
- detailed: Multi-faceted questions needing trend analysis, comparisons, or explanations

Always call the classify_query tool."""

ROUTER_TOOLS = [
    {
        "name": "classify_query",
        "description": "Classify the user's financial query. For compound queries requiring multiple data sources or tickers, set is_compound=true and fill sub_tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["market_data", "knowledge", "general"],
                    "description": "Primary category. For compound queries, this reflects the dominant intent.",
                },
                "ticker": {
                    "type": "string",
                    "description": "Primary stock ticker if identified (BABA, TSLA, etc). Empty if N/A.",
                },
                "company_name": {
                    "type": "string",
                    "description": "Company name if mentioned. Empty if N/A.",
                },
                "query_complexity": {
                    "type": "string",
                    "enum": ["simple", "detailed"],
                    "description": "simple = single-fact lookup, detailed = needs analysis/explanation.",
                },
                "query_summary": {
                    "type": "string",
                    "description": "1-sentence summary of the question.",
                },
                "is_compound": {
                    "type": "boolean",
                    "description": "True if the query requires data from multiple agents or involves multiple tickers that should be compared/analyzed together.",
                },
                "sub_tasks": {
                    "type": "array",
                    "description": "Required when is_compound=true. List of sub-tasks, each specifying which agent to use and what data to collect.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent": {
                                "type": "string",
                                "enum": ["market_data", "knowledge"],
                                "description": "Which agent should handle this sub-task.",
                            },
                            "ticker": {
                                "type": "string",
                                "description": "Ticker for market_data sub-tasks. Empty for knowledge.",
                            },
                            "sub_query": {
                                "type": "string",
                                "description": "What this sub-task should retrieve or answer.",
                            },
                        },
                        "required": ["agent", "sub_query"],
                    },
                },
            },
            "required": ["category", "query_summary"],
        },
    }
]