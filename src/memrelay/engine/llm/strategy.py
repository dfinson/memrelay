"""Pluggable LLM strategy seam (E4-S6 / #63).

Everything downstream of graphiti's entity/edge extraction only ever sees an
``LLMClient``; this module decides *which* concrete client backs it, driven by
``config.llm.strategy`` (``borrow-host`` | ``byo-key`` | ``local``) with a
fallback chain so a misconfigured or unavailable primary degrades gracefully
instead of hard-failing engine construction.

Design notes:
- Each :class:`LLMStrategy` reports ``is_available(cfg)`` cheaply (PATH check,
  env-var presence) and can ``build_client(cfg)``.
- ``build_client`` is always cheap and never touches the network — the byo-key
  client resolves its key lazily and the local client is a stub — so it is safe
  to construct the requested strategy as a last resort even when nothing reports
  availability. That keeps ``search()``/``health()`` working with no LLM present
  while ``note()`` surfaces a clear, strategy-specific error only when invoked.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from graphiti_core.llm_client.client import LLMClient

from memrelay.config import Config

logger = logging.getLogger(__name__)

STRATEGY_BORROW_HOST = "borrow-host"
STRATEGY_BYO_KEY = "byo-key"
STRATEGY_LOCAL = "local"

# Order the chain falls through after the requested strategy is tried first.
_FALLBACK_ORDER = (STRATEGY_BORROW_HOST, STRATEGY_BYO_KEY, STRATEGY_LOCAL)


class LLMStrategy(ABC):
    """One selectable way to obtain a graphiti ``LLMClient``."""

    name: str

    @abstractmethod
    def is_available(self, cfg: Config) -> bool:
        """Return True if this strategy can serve requests in this environment."""

    @abstractmethod
    def build_client(self, cfg: Config) -> LLMClient:
        """Construct the client. Must be cheap and must not touch the network."""


class BorrowHostStrategy(LLMStrategy):
    name = STRATEGY_BORROW_HOST

    def is_available(self, cfg: Config) -> bool:
        from .borrow_host import resolve_host_process

        host_cls = resolve_host_process(cfg.llm.host)
        return host_cls is not None and host_cls.is_installed()

    def build_client(self, cfg: Config) -> LLMClient:
        from .borrow_host import (
            BorrowHostLLMClient,
            _UnknownHostProcess,
            resolve_host_process,
        )

        host_cls = resolve_host_process(cfg.llm.host)
        if host_cls is None:
            # Unknown agent-id: never raise at construction (engine must still build);
            # the fail-loud placeholder surfaces a clear error at extraction time.
            return BorrowHostLLMClient(_UnknownHostProcess(cfg.llm.host))
        return BorrowHostLLMClient(host_cls())


class ByoKeyStrategy(LLMStrategy):
    name = STRATEGY_BYO_KEY

    def is_available(self, cfg: Config) -> bool:
        import os

        env_name = cfg.llm.api_key_env
        return bool(env_name) and bool(os.environ.get(env_name))

    def build_client(self, cfg: Config) -> LLMClient:
        from .byo_key import ByoKeyLLMClient

        return ByoKeyLLMClient(cfg)


class LocalStrategy(LLMStrategy):
    name = STRATEGY_LOCAL

    def is_available(self, cfg: Config) -> bool:
        # Opt-in only (E4-S7 / #64): the local model is selected automatically only
        # when the user explicitly asks for it — ``strategy == "local"`` — or has
        # pointed at a local endpoint via ``local_base_url``. ``local_base_url``
        # defaults to None, so the zero-config default stays borrow-host and the
        # fallback chain / "no LLM present still builds" guarantee are unchanged.
        return cfg.llm.strategy == STRATEGY_LOCAL or bool(cfg.llm.local_base_url)

    def build_client(self, cfg: Config) -> LLMClient:
        from .local import LocalLLMClient

        # Cheap: records base_url/model and constructs the backend (a bare URL
        # holder). No socket is opened until an actual extraction call.
        return LocalLLMClient(base_url=cfg.llm.local_base_url, model=cfg.llm.local_model)


def default_registry() -> dict[str, LLMStrategy]:
    return {
        STRATEGY_BORROW_HOST: BorrowHostStrategy(),
        STRATEGY_BYO_KEY: ByoKeyStrategy(),
        STRATEGY_LOCAL: LocalStrategy(),
    }


def _fallback_chain(requested: str) -> list[str]:
    chain = [requested] if requested in _FALLBACK_ORDER else []
    for name in _FALLBACK_ORDER:
        if name not in chain:
            chain.append(name)
    return chain


def select_llm_client(
    cfg: Config,
    *,
    registry: dict[str, LLMStrategy] | None = None,
) -> LLMClient:
    """Pick and build the ``LLMClient`` for ``cfg`` using the fallback chain.

    Tries the configured strategy first, then the remaining strategies in
    ``_FALLBACK_ORDER``, returning the first whose ``is_available`` is True. If
    none report availability, the requested strategy's client is built anyway
    (construction is cheap/lazy) so failures are explicit and strategy-specific
    at call time rather than a silent ``None``.
    """
    registry = registry or default_registry()
    requested = cfg.llm.strategy or STRATEGY_BORROW_HOST

    for name in _fallback_chain(requested):
        strategy = registry.get(name)
        if strategy is None:
            continue
        try:
            available = strategy.is_available(cfg)
        except Exception as exc:  # noqa: BLE001 - availability probing must never crash selection
            logger.debug("strategy %r availability check failed: %s", name, exc)
            continue
        if available:
            logger.info("Selected LLM strategy: %s", name)
            return strategy.build_client(cfg)

    fallback = requested if requested in registry else STRATEGY_BORROW_HOST
    logger.warning(
        "No LLM strategy reported availability; constructing %r lazily. "
        "note()/extraction will require it to become usable.",
        fallback,
    )
    return registry[fallback].build_client(cfg)
