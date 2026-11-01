"""Reserved phase-three boundary. No sending implementation is shipped."""

from typing import Protocol


class ReplyTransport(Protocol):
    def send(self, group_id: str, text: str, idempotency_key: str) -> None: ...


class DisabledReplies:
    def send(self, group_id: str, text: str, idempotency_key: str) -> None:
        raise RuntimeError("自动回复未实现/未启用；须完成 Windows 实测并经用户明确启用")
