import json
import asyncio


class DataChannelMixin:

    def _on_datachannel(self, channel) -> None:
        self._dc = channel
        self._dc.on("message", self._on_dc_message)
        self._dc.on("close", self._on_dc_close)
        asyncio.ensure_future(self._start_interview())
        self._start_interview_timer()

    def _on_dc_message(self, message) -> None:
        data = json.loads(message)
        msg_type = data.get("type")

        if msg_type == "PTT_START":
            self._start_recording()
        elif msg_type == "PTT_END":
            self._stop_recording()

    def _on_dc_close(self) -> None:
        self._dc = None

    def send_dc(self, data: dict) -> None:
        if self._dc and self._dc.readyState == "open":
            self._dc.send(json.dumps(data, ensure_ascii=False))

    async def send_dc_async(self, data: dict, timeout: float = 10.0) -> None:
        elapsed = 0.0
        while elapsed < timeout:
            if self._dc and self._dc.readyState == "open":
                self._dc.send(json.dumps(data, ensure_ascii=False))
                return
            await asyncio.sleep(0.1)
            elapsed += 0.1
