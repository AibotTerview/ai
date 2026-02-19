import io
import logging

import pdfplumber
import requests

logger = logging.getLogger(__name__)

_MAX_CHARS = 8_000   # 너무 긴 이력서가 프롬프트를 초과하지 않도록 제한
_DOWNLOAD_TIMEOUT = 10  # 초


def extract_resume_text(resume_uri: str) -> str:
    """
    S3 Public URL에서 PDF를 다운로드해 텍스트를 추출한다.
    실패 시 빈 문자열 반환 (예외 미전파).
    """
    if not resume_uri:
        return ""

    try:
        response = requests.get(resume_uri, timeout=_DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        pdf_bytes = response.content
    except Exception as e:
        logger.warning(f"[ResumeExtractor] PDF 다운로드 실패 '{resume_uri}': {e}")
        return ""

    try:
        text_parts = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        full_text = "\n".join(text_parts).strip()
        return full_text[:_MAX_CHARS]
    except Exception as e:
        logger.warning(f"[ResumeExtractor] PDF 파싱 실패 '{resume_uri}': {e}")
        return ""
