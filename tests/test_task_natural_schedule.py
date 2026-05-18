from __future__ import annotations

from weclaw.tasks.scheduler import parse_natural_schedule


def test_parse_natural_schedule_accepts_reminder_prefixes() -> None:
    parsed = parse_natural_schedule("记得20秒后提醒我喝水")

    assert parsed is not None
    assert parsed.prompt == "提醒我喝水"
    assert parsed.schedule_type == "once"
