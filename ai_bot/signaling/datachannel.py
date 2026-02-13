import json
import logging
import asyncio

logger = logging.getLogger(__name__)


class DataChannelMixin:
    """DataChannel 관리 mixin — WebRTCSession에서 사용"""

    def _on_datachannel(self, channel) -> None:
        logger.info(f"[DataChannel 수신] label={channel.label}")
        self._dc = channel
        self._dc.on("message", self._on_dc_message)
        self._dc.on("close", self._on_dc_close)

        # 면접 세션 시작 + 타이머
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(self._start_interview(), loop=loop)
        self._start_interview_timer()

    def _on_dc_message(self, message) -> None:
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[DC] 파싱 실패: {message}")
            return

        msg_type = data.get("type")
        logger.info(f"[DC 수신] type={msg_type}")

        if msg_type == "PTT_START":
            self._start_recording()
        elif msg_type == "PTT_END":
            self._stop_recording()

    def _on_dc_close(self) -> None:
        logger.info("[DataChannel 종료]")
        self._dc = None

    def send_dc(self, data: dict) -> None:
        """DataChannel로 JSON 메시지 전송"""
        if self._dc and self._dc.readyState == "open":
            self._dc.send(json.dumps(data, ensure_ascii=False))
        else:
            logger.warning(f"[DC] 채널이 열려있지 않아 전송 불가: {data.get('type')}")

    async def send_dc_async(self, data: dict, timeout: float = 10.0) -> bool:
        """DataChannel이 열릴 때까지 대기 후 전송"""
        elapsed = 0.0
        while elapsed < timeout:
            if self._dc and self._dc.readyState == "open":
                self._dc.send(json.dumps(data, ensure_ascii=False))
                return True
            await asyncio.sleep(0.1)
            elapsed += 0.1
        logger.warning(f"[DC] 타임아웃 — 전송 실패: {data.get('type')}")
        return False
