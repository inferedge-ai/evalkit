from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.core.agents import AgentDefinition
from evalkit.credentials import Kind
from evalkit.db.agents import AgentStore
from evalkit.db.crypto import SecretBox
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.llm_providers import LlmProviderStore
from evalkit.db.trace_sources import EncryptionKeyMissing


class StoredKeys:
    def __init__(self, keys: dict[tuple[Kind, str], str]):
        self._keys = keys

    def key_for(self, kind: Kind, ref: str) -> str | None:
        return self._keys.get((kind, ref))

    def require_judge_key(self, base_url: str) -> str | None:
        return self._keys.get(("llm_provider", base_url))


class NoKeysLoaded:
    def key_for(self, kind: Kind, ref: str) -> str | None:
        raise RuntimeError("server keys are loaded per experiment with KeyLoader.load")

    def require_judge_key(self, base_url: str) -> str | None:
        raise RuntimeError("server keys are loaded per experiment with KeyLoader.load")


class KeyLoader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], box: SecretBox | None):
        self._agents = AgentStore(session_factory)
        self._evaluators = RemoteEvaluatorStore(session_factory)
        self._providers = LlmProviderStore(session_factory)
        self._box = box

    def _open(self, sealed: bytes) -> str:
        if self._box is None:
            raise EncryptionKeyMissing()
        return self._box.open(sealed)

    def seal(self, plain: str) -> bytes:
        if self._box is None:
            raise EncryptionKeyMissing()
        return self._box.seal(plain)

    async def agent_key(self, agent: AgentDefinition) -> str | None:
        if not agent.workspace_is_shared:
            return None
        sealed = await self._agents.sealed_key(agent.ref)
        return self._open(sealed) if sealed is not None else None

    async def load(self, agent: AgentDefinition, judge_base_url: str | None) -> StoredKeys:
        keys: dict[tuple[Kind, str], str] = {}
        if not agent.workspace_is_shared:
            return StoredKeys(keys)
        sealed_agent = await self._agents.sealed_key(agent.ref)
        if sealed_agent is not None:
            keys["agent", agent.ref] = self._open(sealed_agent)
        for name, sealed in (await self._evaluators.sealed_keys(agent.workspace)).items():
            keys["evaluator", f"{name}@{agent.workspace}"] = self._open(sealed)
        if judge_base_url is not None:
            sealed_provider = await self._providers.sealed_key(judge_base_url)
            if sealed_provider is not None:
                keys["llm_provider", judge_base_url] = self._open(sealed_provider)
        return StoredKeys(keys)
