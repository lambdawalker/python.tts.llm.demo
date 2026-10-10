import numpy as np
import pytest
import soundfile as sf


def test_workspace_survives_restart(tmp_path):
    from tts_llm_demo.store import Store

    store = Store(tmp_path)
    chat = store.new_chat()
    store.message(chat["id"], "user", "Make a negotiation")
    store.close()
    reopened = Store(tmp_path)
    assert reopened.messages(chat["id"])[0]["content"] == "Make a negotiation"
    reopened.close()


def test_yaml_resolves_storage_relative_to_config(tmp_path):
    from tts_llm_demo.config import load_config

    path = tmp_path / "demo.yaml"
    path.write_text("llm:\n  base_url: http://spark:8001/v1\n  model: qwen\n")
    config = load_config(path)
    assert config.storage_dir == tmp_path / ".demo-data"
    assert config.host == "127.0.0.1"
    path.write_text("llm:\n  base_url: file:///tmp/bad\n  model: qwen\n")
    with pytest.raises(ValueError):
        load_config(path)


def test_audio_order_pause_and_mismatched_rates(tmp_path):
    from tts_llm_demo.audio import assemble

    a, b, out = [tmp_path / p for p in ("a.wav", "b.wav", "out.wav")]
    sf.write(a, np.ones(2400) * 0.25, 24000)
    sf.write(b, np.ones(2400) * -0.25, 24000)
    assemble([a, b], out, 100, 10)
    data, rate = sf.read(out)
    assert rate == 24000 and len(data) == 7200
    assert data[:2400].mean() > 0.24
    assert not data[2400:4800].any()
    assert data[4800:].mean() < -0.24
    sf.write(b, np.ones(100), 16000)
    with pytest.raises(ValueError, match="sample rate"):
        assemble([a, b], out, 100, 10)
