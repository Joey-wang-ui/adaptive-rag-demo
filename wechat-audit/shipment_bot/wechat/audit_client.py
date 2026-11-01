"""Authenticated internal API bridge; network failure leaves inbox available for retry."""

from urllib.parse import urlsplit

import httpx

from shipment_bot.models import Document


class LocalAuditClient:
    def __init__(self, base_url: str, token: str, *, transport=None):
        url = urlsplit(base_url)
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("internal audit API must use loopback")
        if url.username or url.password or url.query or url.fragment or len(token) < 32:
            raise ValueError("invalid local API configuration")
        self.base_url, self.token, self.transport = base_url, token, transport

    def audit(self, task_id: str, order_id: str, delivery: Document, source: str):
        if source != "wechat":
            raise ValueError("wechat ingress only")
        with httpx.Client(
            base_url=self.base_url, trust_env=False, timeout=30, transport=self.transport
        ) as client:
            response = client.post(
                "/ingress/wechat/audits",
                headers={"Authorization": "Bearer " + self.token},
                json={
                    "task_id": task_id,
                    "order_id": order_id,
                    "delivery": delivery.model_dump(mode="json"),
                },
            )
            response.raise_for_status()
            return response.json()
