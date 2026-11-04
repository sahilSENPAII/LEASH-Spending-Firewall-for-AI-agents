from leash_agent.config import REPO_ROOT, AgentSettings, leash_connection


def _settings(**kw):
    return AgentSettings(_env_file=None, **kw)


def test_default_starts_the_backend_mcp_server_over_stdio():
    connection = leash_connection(_settings())
    assert connection["transport"] == "stdio" and connection["command"] == "uv"
    assert connection["args"] == [
        "run", "--frozen", "--directory", str(REPO_ROOT / "backend"),
        "python", "-m", "app.mcp_server",
    ]
    assert connection["env"]["FASTMCP_LOG_LEVEL"] == "WARNING"
    assert "PATH" in connection["env"]


def test_one_setting_switches_to_http():
    url = "https://leash.onrender.com/mcp"
    connection = leash_connection(_settings(leash_url=url))
    assert connection == {"transport": "streamable_http", "url": url}


def test_env_names(monkeypatch):
    monkeypatch.setenv("AGENT_LEASH_URL", "https://x/mcp")
    monkeypatch.setenv("AGENT_MANDATE_ID", "m_other")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    s = _settings()
    assert (s.leash_url, s.mandate_id, s.groq_api_key) == ("https://x/mcp", "m_other", "k")


def test_default_stores_are_sahils_ports():
    assert _settings().stores() == {
        "soundhub": "http://localhost:8101",
        "megadeals": "http://localhost:8102",
    }


def test_http_sends_the_bearer_token():
    connection = leash_connection(_settings(leash_url="https://x/mcp", leash_token="t0ken"))
    assert connection == {
        "transport": "streamable_http",
        "url": "https://x/mcp",
        "headers": {"Authorization": "Bearer t0ken"},
    }
