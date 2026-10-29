import json

import bootstrap


def test_registration_requires_real_loaded_hook_metadata():
    assert not bootstrap.plugin_registered("error: inbound_claim not supported")
    assert not bootstrap.plugin_registered(
        json.dumps(
            {
                "plugin": {"id": "jiahui-shipment", "status": "error"},
                "typedHooks": [{"name": "inbound_claim"}],
            }
        )
    )
    assert not bootstrap.plugin_registered(
        json.dumps({"plugin": {"id": "jiahui-shipment", "status": "loaded"}, "typedHooks": []})
    )
    assert bootstrap.plugin_registered(
        json.dumps(
            {
                "plugin": {"id": "jiahui-shipment", "status": "loaded"},
                "typedHooks": [{"name": "inbound_claim"}],
            }
        )
    )


def test_first_run_diagnostics_do_not_hide_valid_hook_metadata():
    result = {
        "plugin": {"id": "jiahui-shipment", "status": "loaded"},
        "typedHooks": [{"name": "inbound_claim"}],
    }
    assert bootstrap.plugin_registered(
        "[postinstall] native support warning\n" + json.dumps(result)
    )


def test_qr_readiness_requires_a_real_account_index(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "DATA", tmp_path)
    assert not bootstrap.has_wechat_account()
    account_file = tmp_path / "openclaw/openclaw-weixin/accounts.json"
    account_file.parent.mkdir(parents=True)
    account_file.write_text("[]")
    assert not bootstrap.has_wechat_account()
    account_file.write_text('["test-account"]')
    assert bootstrap.has_wechat_account()


def test_restart_preserves_official_channel_accounts_and_never_embeds_api_key(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bootstrap, "DATA", tmp_path)
    monkeypatch.setattr(bootstrap, "free_port", lambda start: start)
    profile = tmp_path / "openclaw"
    profile.mkdir()
    config_path = profile / "openclaw.json"
    channels = {"openclaw-weixin": {"accounts": {"bot1": {"enabled": True}}}}
    config_path.write_text(json.dumps({"channels": channels}))
    env, port = bootstrap.configure("private-test-api-key")
    config = json.loads(config_path.read_text())
    assert config["channels"] == channels
    assert "private-test-api-key" not in config_path.read_text()
    assert env["DEEPSEEK_API_KEY"] == "private-test-api-key"
    assert config["gateway"]["bind"] == "loopback"
    assert config["tools"]["deny"] == ["*"]
    assert port == 18940
