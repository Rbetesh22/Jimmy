from .base import Document


class NotionIngester:
    def __init__(self, api_token: str):
        from notion_client import Client
        self.notion = Client(auth=api_token)

    def ingest(self) -> list[Document]:
        docs = []
        cursor = None
        while True:
            kwargs = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self.notion.search(**kwargs)
            for page in resp.get("results", []):
                try:
                    doc = self._process_page(page)
                    if doc:
                        docs.append(doc)
                except Exception as e:
                    print(f"  Warning: {e}")
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return docs

    def _process_page(self, page: dict) -> Document | None:
        page_id = page["id"]
        props = page.get("properties", {})
        title = self._extract_title(props) or "Untitled"
        content = self._get_blocks_text(page_id)
        if len(content) < 50:
            return None
        return Document(
            id=f"notion_{page_id.replace('-', '')}",
            content=f"{title}\n\n{content}",
            source="notion",
            title=f"Notion: {title}",
            metadata={
                "type": "page",
                "url": page.get("url", ""),
                "created": page.get("created_time", "")[:10],
            },
        )

    def _extract_title(self, props: dict) -> str:
        for val in props.values():
            if val.get("type") == "title":
                return "".join(t.get("plain_text", "") for t in val.get("title", []))
        return ""

    def _get_blocks_text(self, page_id: str) -> str:
        blocks = self.notion.blocks.children.list(block_id=page_id, page_size=100)
        return "\n\n".join(
            filter(None, [self._block_to_text(b) for b in blocks.get("results", [])])
        )

    def _block_to_text(self, block: dict) -> str:
        btype = block.get("type", "")
        data = block.get(btype, {})
        rich_text = data.get("rich_text", [])
        text = "".join(t.get("plain_text", "") for t in rich_text)
        # Add prefix for structural blocks
        if btype in ("heading_1", "heading_2", "heading_3"):
            text = f"## {text}"
        elif btype == "bulleted_list_item":
            text = f"• {text}"
        elif btype == "numbered_list_item":
            text = f"- {text}"
        return text
