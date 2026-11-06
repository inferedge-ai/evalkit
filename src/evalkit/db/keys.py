from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.core.agents import AgentDefinition
from evalkit.credentials import Kind
from evalkit.db.agents import AgentStore
from evalkit.db.crypto import SecretBox
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.llm_providers import LlmProviderStore, UnknownLlmProvider
from evalkit.db.trace_sources import EncryptionKeyMissing


class StoredKeys:
    def __init__(
        self,
        keys: dict[tuple[Kind, str], tuple[str, str]],
        providers: dict[str, tuple[str, str | None]] | None = None,
    ):
        self._keys = keys
        self._providers = providers or {}

    def key_for(self, kind: Kind, ref: str, url: str | None = None) -> str | None:
        stored = self._keys.get((kind, ref))
        if stored is None:
            return None
        key, stored_url = stored
        return key if url is None or url == stored_url else None

    def require_judge_key(self, base_url: str) -> str | None:
        raise UnknownLlmProvider(base_url)

    def provider_judge(self, name: str) -> tuple[str, str | None]:
        if name not in self._providers:
            raise UnknownLlmProvider(name)
        return self._providers[name]


class NoKeysLoaded:
    def key_for(self, kind: Kind, ref: str, url: str | None = None) -> str | None:
        raise RuntimeError("server keys are loaded per experiment with KeyLoader.load")

    def require_judge_key(self, base_url: str) -> str | None:
        raise RuntimeError("server keys are loaded per experiment with KeyLoader.load")

    def provider_judge(self, name: str) -> tuple[str, str | None]:
        raise RuntimeError("server keys are loaded per experiment with KeyLoader.load")


class KeyLoader:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tenant_id: str,
        box: SecretBox | None,
    ):
        self._agents = AgentStore(session_factory, tenant_id)
        self._evaluators = RemoteEvaluatorStore(session_factory, tenant_id)
        self._providers = LlmProviderStore(session_factory, tenant_id)
        self._box = box

    def _open(self, sealed: bytes) -> str:
        if self._box is None:
            raise EncryptionKeyMissing()
        return self._box.open(sealed)

    def seal(self, plain: str | None) -> bytes | None:
        if not plain:
            return None
        if self._box is None:
            raise EncryptionKeyMissing()
        return self._box.seal(plain)

    async def agent_key(self, agent: AgentDefinition) -> str | None:
        if not agent.workspace_is_shared:
            return None
        sealed = await self._agents.sealed_key(agent.ref)
        return self._open(sealed) if sealed is not None else None

    async def provider_judges(self, name: str | None) -> dict[str, tuple[str, str | None]]:
        if name is None:
            return {}
        endpoint = await self._providers.active_endpoint(name)
        if endpoint is None:
            return {}
        base_url, sealed = endpoint
        return {name: (base_url, self._open(sealed) if sealed is not None else None)}

    async def load(self, agent: AgentDefinition, judge_provider: str | None) -> StoredKeys:
        keys: dict[tuple[Kind, str], tuple[str, str]] = {}
        providers = await self.provider_judges(judge_provider)
        if not agent.workspace_is_shared:
            return StoredKeys(keys, providers)
        sealed_agent = await self._agents.sealed_key(agent.ref)
        if sealed_agent is not None and agent.harness_url is not None:
            keys["agent", agent.ref] = (self._open(sealed_agent), agent.harness_url)
        for name, (endpoint, sealed) in (
            await self._evaluators.sealed_keys(agent.workspace)
        ).items():
            keys["evaluator", f"{name}@{agent.workspace}"] = (self._open(sealed), endpoint)
        return StoredKeys(keys, providers)
