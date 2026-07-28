from events import EventBuffer, TaskEvent, MAX_EVENTS_PER_TASK


def test_event_cursor_survives_ring_buffer_eviction():
    buffer = EventBuffer()
    task_id = "task-events"
    for number in range(MAX_EVENTS_PER_TASK + 5):
        buffer.append(TaskEvent(task_id, "stream", data={"number": number}))

    events, cursor = buffer.get_since(task_id, 0)
    assert len(events) == MAX_EVENTS_PER_TASK
    assert events[0]["sequence"] == 6
    assert cursor == MAX_EVENTS_PER_TASK + 5

    buffer.append(TaskEvent(task_id, "completed"))
    new_events, new_cursor = buffer.get_since(task_id, cursor)
    assert [event["type"] for event in new_events] == ["completed"]
    assert new_cursor == cursor + 1


def test_event_clear_resets_sequence():
    buffer = EventBuffer()
    buffer.append(TaskEvent("task-clear", "created"))
    buffer.clear("task-clear")
    buffer.append(TaskEvent("task-clear", "created"))
    events, cursor = buffer.get_since("task-clear", 0)
    assert events[0]["sequence"] == 1
    assert cursor == 1
