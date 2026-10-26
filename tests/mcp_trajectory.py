def chat_session() -> dict:
    return {
        "schema_version": "ATIF-v1.8",
        "session_id": "sess-chat",
        "agent": {"name": "chat", "model_name": "judge-model"},
        "steps": [
            {
                "step_id": 1,
                "timestamp": "2026-10-20T23:00:00Z",
                "source": "user",
                "message": "Which documents mention the refund policy?",
            },
            {
                "step_id": 2,
                "timestamp": "2026-10-20T23:00:04Z",
                "source": "agent",
                "message": "Screening.",
                "tool_calls": [
                    {
                        "tool_call_id": "c1",
                        "function_name": "search_docs",
                        "arguments": {"query": "x" * 5000},
                    },
                    {
                        "tool_call_id": "c2",
                        "function_name": "search_docs",
                        "arguments": {"query": "y"},
                    },
                    {"tool_call_id": "c3", "function_name": "Bash", "arguments": {"cmd": "ls"}},
                ],
                "observation": {
                    "results": [
                        {
                            "source_call_id": "c1",
                            "content": "unknown_column: name",
                            "extra": {"is_error": True},
                        },
                        {
                            "source_call_id": "c2",
                            "content": "unknown_column: aum",
                            "extra": {"is_error": True},
                        },
                        {
                            "source_call_id": "c3",
                            "content": "Shell command failed",
                            "extra": {"is_error": True},
                        },
                    ]
                },
                "metrics": {"prompt_tokens": 1000, "completion_tokens": 100, "cost_usd": 0.5},
            },
            {
                "step_id": 3,
                "timestamp": "2026-10-20T23:00:30Z",
                "source": "agent",
                "message": "Two documents mention it.",
                "metrics": {"prompt_tokens": 2000, "completion_tokens": 200},
            },
            {
                "step_id": 4,
                "timestamp": "2026-10-20T23:05:00Z",
                "source": "user",
                "message": "Put it in Excel.",
            },
            {
                "step_id": 5,
                "timestamp": "2026-10-20T23:06:40Z",
                "source": "system",
                "message": "This turn ran past its 60-minute limit and was stopped.",
                "extra": {"is_error": True},
            },
        ],
    }
