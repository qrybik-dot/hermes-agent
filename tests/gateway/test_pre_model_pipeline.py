from gateway.pre_model_pipeline import PreModelStage, run_pre_model_pipeline


def test_pipeline_skips_disabled_and_stops_at_first_response():
    calls = []

    def stage(name, response=None):
        def run():
            calls.append(name)
            return response
        return run

    outcome = run_pre_model_pipeline([
        PreModelStage("disabled", stage("disabled"), enabled=False),
        PreModelStage("location", stage("location")),
        PreModelStage("save_intent", stage("save_intent", {"status": "ok"})),
        PreModelStage("uncertainty", stage("uncertainty", {"status": "late"})),
    ])

    assert calls == ["location", "save_intent"]
    assert outcome.stage == "save_intent"
    assert outcome.response == {"status": "ok"}


def test_pipeline_returns_none_when_no_stage_handles_turn():
    assert run_pre_model_pipeline([
        PreModelStage("one", lambda: None),
        PreModelStage("two", lambda: None),
    ]) is None
