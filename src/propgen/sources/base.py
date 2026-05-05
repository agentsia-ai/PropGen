"""Abstract bases for inbound proposal channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from propgen.models import RawProposalRequest


class ProposalWebhookReceiver(ABC):
    """HTTP layer calls `handle` with raw body + headers."""

    @abstractmethod
    async def handle(self, body: bytes, headers: dict[str, str]) -> RawProposalRequest:
        raise NotImplementedError
