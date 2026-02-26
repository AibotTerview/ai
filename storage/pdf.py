import io
import logging

import boto3
import fitz  # pymupdf
from django.conf import settings

logger = logging.getLogger(__name__)

MAX_RESUME_CHARS = 8000  # LLM 컨텍스트 과부하 방지


def extract_text_from_s3(s3_uri: str) -> str:
    """
    S3 URI에서 PDF를 다운로드하고 텍스트를 추출합니다.
    s3_uri 형식: https://<bucket>.s3.<region>.amazonaws.com/<key>
    """
    try:
        key = _parse_s3_key(s3_uri)
        if not key:
            logger.warning("[PDF] S3 URI에서 키 추출 실패: %s", s3_uri)
            return ""

        s3 = boto3.client(
            "s3",
            region_name=settings.AWS_S3_REGION_NAME,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

        buf = io.BytesIO()
        s3.download_fileobj(settings.AWS_STORAGE_BUCKET_NAME, key, buf)
        buf.seek(0)

        text = _extract_text(buf.read())
        if len(text) > MAX_RESUME_CHARS:
            text = text[:MAX_RESUME_CHARS] + "\n... (이하 생략)"

        logger.info("[PDF] 텍스트 추출 완료 (%d자): %s", len(text), key)
        return text

    except Exception as e:
        logger.error("[PDF] 텍스트 추출 실패: %s", e)
        return ""


def _parse_s3_key(s3_uri: str) -> str | None:
    """https://<bucket>.s3.<region>.amazonaws.com/<key> 에서 key 추출."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(s3_uri)
        # path는 /<key> 형태
        key = parsed.path.lstrip("/")
        return key if key else None
    except Exception:
        return None


def _extract_text(pdf_bytes: bytes) -> str:
    """pymupdf로 PDF 바이트에서 텍스트 추출."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages).strip()
