from photolib.actions.base import ProgressEvent
from photolib.actions.phases import phase_label, run_phase


def _sub(events):
    def runner(ctx, params):
        yield from events
    return runner


def test_progress_is_rescaled_into_the_span():
    events = [ProgressEvent("a", progress=0.0), ProgressEvent("b", progress=1.0)]
    out = list(run_phase("Scan", (0.2, 0.6), _sub(events), None, None,
                         index=2, total=5))
    assert [e.progress for e in out] == [0.2, 0.6]


def test_midpoint_lands_in_the_middle_of_the_span():
    events = [ProgressEvent("a", progress=0.5)]
    out = list(run_phase("Scan", (0.0, 1.0), _sub(events), None, None,
                         index=1, total=1))
    assert out[0].progress == 0.5


def test_none_progress_passes_through():
    events = [ProgressEvent("a")]
    out = list(run_phase("Scan", (0.2, 0.6), _sub(events), None, None,
                         index=2, total=5))
    assert out[0].progress is None


def test_phase_label_is_attached_and_the_message_is_untouched():
    events = [ProgressEvent("indexing IMG_1.HEIC", progress=0.5, level="warn")]
    out = list(run_phase("Scan", (0.0, 1.0), _sub(events), None, None,
                         index=2, total=5))
    assert out[0].phase == "Scan (2/5)"
    assert out[0].message == "indexing IMG_1.HEIC"
    assert out[0].level == "warn"


def test_item_counts_pass_through():
    events = [ProgressEvent("a", progress=0.5, done=3, total=9)]
    out = list(run_phase("Scan", (0.0, 1.0), _sub(events), None, None,
                         index=1, total=1))
    assert (out[0].done, out[0].total) == (3, 9)


def test_phase_label_format():
    assert phase_label("Upload", 5, 5) == "Upload (5/5)"
