"""Document OCR ingester — uses Claude Vision to extract text from images."""
import base64
from pathlib import Path
from .base import Document, _h


class DocumentOCRIngester:
    def ingest(self, path: str) -> list[Document]:
        """OCR one or more image files using Claude Vision API.

        path: single image file or directory of images
        """
        p = Path(path)
        if p.is_dir():
            images = []
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.heic", "*.webp", "*.tiff"):
                images.extend(p.glob(ext))
            images.sort()
        else:
            images = [p]

        docs = []
        for img_path in images:
            try:
                text = self._ocr_image(img_path)
                if text and len(text.strip()) >= 30:
                    docs.append(Document(
                        id=f"ocr_{_h(str(img_path))}",
                        content=text,
                        source="file",
                        title=f"OCR: {img_path.stem}",
                        metadata={
                            "type": "ocr_document",
                            "original_path": str(img_path),
                        },
                    ))
            except Exception as e:
                print(f"  OCR failed for {img_path.name}: {e}")

        return docs

    def _ocr_image(self, path: Path) -> str:
        """Send image to Claude Vision for text extraction."""
        import os
        from anthropic import Anthropic

        # Read and encode image
        data = path.read_bytes()
        b64 = base64.standard_b64encode(data).decode("utf-8")

        suffix = path.suffix.lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".heic": "image/heic",
            ".tiff": "image/tiff",
        }
        media_type = media_types.get(suffix, "image/jpeg")

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            # Try loading from .env
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).parent.parent.parent / ".env")
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extract ALL text from this document image. "
                            "Preserve the structure (headings, paragraphs, lists, tables). "
                            "If this is a legal document, contract, or form, include all fields and values. "
                            "Output only the extracted text, nothing else."
                        ),
                    },
                ],
            }],
        )
        return response.content[0].text
