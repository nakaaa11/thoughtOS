import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from src.config import load_settings
from src.db import ThoughtDB
from src.embedder import Embedder

settings = load_settings()
db = ThoughtDB(settings.database_url)
embedder = Embedder(settings.voyage_api_key, settings.voyage_model)

server = Server("thought-os")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_thoughts",
            description="過去の思考・会話・検索をキーワードまたは意味的類似度で検索する",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ"},
                    "source_type": {
                        "type": "string",
                        "description": "ソースタイプで絞り込み: claude, google_search, google_browse",
                        "enum": ["claude", "google_search", "google_browse"],
                    },
                    "category": {"type": "string", "description": "カテゴリで絞り込み"},
                    "limit": {
                        "type": "integer",
                        "description": "最大件数",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="search_sessions",
            description="思考セッション（複数ソースを統合した思考の流れ）を検索する",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ"},
                    "limit": {
                        "type": "integer",
                        "description": "最大件数",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="browse_by_tag",
            description="タグでエントリを絞り込む。引数なしでタグ一覧を返す",
            inputSchema={
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "タグ名（省略でタグ一覧）"},
                    "limit": {
                        "type": "integer",
                        "description": "最大件数",
                        "default": 20,
                    },
                },
            },
        ),
        Tool(
            name="browse_by_period",
            description="期間を指定してエントリを閲覧する",
            inputSchema={
                "type": "object",
                "properties": {
                    "start": {
                        "type": "string",
                        "description": "開始日 (ISO 8601, 例: 2025-01-01)",
                    },
                    "end": {
                        "type": "string",
                        "description": "終了日 (ISO 8601, 例: 2025-02-01)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最大件数",
                        "default": 50,
                    },
                },
                "required": ["start", "end"],
            },
        ),
        Tool(
            name="get_thinking_pattern",
            description="特定のClaude会話の思考パターン詳細を返す",
            inputSchema={
                "type": "object",
                "properties": {
                    "entry_id": {
                        "type": "string",
                        "description": "thought_entryのUUID",
                    },
                },
                "required": ["entry_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_thoughts":
        return await _search_thoughts(arguments)
    elif name == "search_sessions":
        return await _search_sessions(arguments)
    elif name == "browse_by_tag":
        return await _browse_by_tag(arguments)
    elif name == "browse_by_period":
        return await _browse_by_period(arguments)
    elif name == "get_thinking_pattern":
        return await _get_thinking_pattern(arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _search_thoughts(args: dict) -> list[TextContent]:
    query = args["query"]
    source_type = args.get("source_type")
    category = args.get("category")
    limit = args.get("limit", 5)

    # ベクトル検索
    query_embedding = embedder.embed(query)
    vector_results = db.search_by_similarity(
        query_embedding, limit=limit, source_type=source_type
    )

    # キーワード検索
    keyword_results = db.search_by_keyword(
        query, limit=limit, source_type=source_type, category=category
    )

    # マージ (重複除去)
    seen_ids = set()
    merged = []
    for r in vector_results + keyword_results:
        rid = str(r.get("id", ""))
        if rid not in seen_ids:
            seen_ids.add(rid)
            merged.append(r)

    # 上位limit件に制限
    merged = merged[:limit]

    output = _format_entries(merged)
    return [TextContent(type="text", text=output)]


async def _search_sessions(args: dict) -> list[TextContent]:
    query = args["query"]
    limit = args.get("limit", 5)

    query_embedding = embedder.embed(query)
    results = db.search_sessions_by_similarity(query_embedding, limit=limit)

    lines = []
    for s in results:
        lines.append(f"## {s.get('topic', 'N/A')}")
        lines.append(f"期間: {s.get('timeframe_start', '')} 〜 {s.get('timeframe_end', '')}")
        lines.append(f"ソース: {', '.join(s.get('sources', []))}")
        lines.append(f"ストーリー: {s.get('narrative', '')}")
        lines.append(f"タグ: {', '.join(s.get('tags', []))}")
        lines.append("")

    return [TextContent(type="text", text="\n".join(lines) or "該当するセッションが見つかりませんでした。")]


async def _browse_by_tag(args: dict) -> list[TextContent]:
    tag = args.get("tag")
    limit = args.get("limit", 20)

    if not tag:
        tags = db.get_all_tags()
        lines = ["# タグ一覧", ""]
        for t in tags:
            lines.append(f"- {t['tag']} ({t['count']}件)")
        return [TextContent(type="text", text="\n".join(lines))]

    results = db.browse_by_tag(tag, limit=limit)
    output = f"# タグ: {tag}\n\n" + _format_entries(results)
    return [TextContent(type="text", text=output)]


async def _browse_by_period(args: dict) -> list[TextContent]:
    start = args["start"]
    end = args["end"]
    limit = args.get("limit", 50)

    results = db.browse_by_period(start, end, limit=limit)
    output = f"# 期間: {start} 〜 {end}\n\n" + _format_entries(results)
    return [TextContent(type="text", text=output)]


async def _get_thinking_pattern(args: dict) -> list[TextContent]:
    entry_id = args["entry_id"]
    entry = db.get_entry(entry_id)

    if not entry:
        return [TextContent(type="text", text="エントリが見つかりませんでした。")]

    lines = [f"# {entry.get('title', 'N/A')}"]
    lines.append(f"日時: {entry.get('created_at', '')}")
    lines.append(f"要約: {entry.get('summary', 'N/A')}")
    lines.append("")

    pattern = entry.get("thinking_pattern")
    if pattern:
        if isinstance(pattern, str):
            pattern = json.loads(pattern)
        lines.append("## 思考パターン")
        lines.append(f"質問スタイル: {pattern.get('question_style', 'N/A')}")
        lines.append(f"深掘りポイント: {', '.join(pattern.get('deepening_points', []))}")
        lines.append(f"判断基準: {', '.join(pattern.get('decision_criteria', []))}")
        lines.append(f"繰り返しテーマ: {', '.join(pattern.get('recurring_themes', []))}")
        lines.append(f"思考の癖: {', '.join(pattern.get('thinking_habits', []))}")
        lines.append(f"価値観: {', '.join(pattern.get('values_expressed', []))}")
        lines.append(f"知識ギャップ: {', '.join(pattern.get('knowledge_gaps', []))}")
        lines.append(f"行動傾向: {pattern.get('action_tendency', 'N/A')}")
    else:
        lines.append("思考パターンデータなし（Claude会話でないか、ターン数不足）")

    return [TextContent(type="text", text="\n".join(lines))]


def _format_entries(entries: list[dict]) -> str:
    if not entries:
        return "該当するエントリが見つかりませんでした。"

    lines = []
    for e in entries:
        lines.append(f"### {e.get('title', 'N/A')}")
        lines.append(f"ID: {e.get('id', '')}")
        lines.append(f"ソース: {e.get('source_type', '')} | カテゴリ: {e.get('category', 'N/A')}")
        lines.append(f"日時: {e.get('created_at', '')}")
        lines.append(f"要約: {e.get('summary', 'N/A')}")
        lines.append(f"タグ: {', '.join(e.get('tags', []))}")
        lines.append("")
    return "\n".join(lines)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
