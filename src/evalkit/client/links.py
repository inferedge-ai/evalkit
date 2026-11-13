from urllib.parse import quote


def _encode_component(segment: str) -> str:
    return quote(segment, safe="!*'()")


def _join(server_url: str, *segments: str) -> str:
    return "/".join([server_url.rstrip("/"), *(_encode_component(s) for s in segments)])


def build_experiment_url(server_url: str, exp_id: int) -> str:
    return _join(server_url, "experiments", str(exp_id))


def build_eval_run_url(server_url: str, exp_id: int, case_id: str) -> str:
    return _join(server_url, "experiments", str(exp_id), case_id)


def build_dataset_url(server_url: str, dataset_ref: str) -> str:
    return _join(server_url, "datasets", dataset_ref)


def build_case_url(server_url: str, dataset_ref: str, case_id: str) -> str:
    return _join(server_url, "datasets", dataset_ref, "cases", case_id)


def build_agent_url(server_url: str, agent_ref: str) -> str:
    return _join(server_url, "agents", agent_ref)


def build_agent_run_url(server_url: str, agent_ref: str, run_id: str) -> str:
    return _join(server_url, "runs", agent_ref, run_id)
