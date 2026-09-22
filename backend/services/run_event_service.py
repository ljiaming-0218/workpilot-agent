"""Thread-safe ephemeral progress events for Server-Sent Events clients."""

import json
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Condition
from time import monotonic
from typing import Any


@dataclass(frozen=True, slots=True)
class RunProgressEvent:
    sequence: int
    event_type: str
    data: dict[str, Any]


class RunEventBroker:
    """Keep bounded in-process event history without becoming durable state."""

    def __init__(self, max_events_per_run: int = 200) -> None:
        self._condition = Condition()
        self._events: dict[int, list[RunProgressEvent]] = defaultdict(list)
        self._next_sequence: dict[int, int] = defaultdict(lambda: 1)
        self._terminal_runs: set[int] = set()
        self._max_events = max_events_per_run

    def publish(
        self,
        run_id: int,
        event_type: str,
        data: dict[str, Any],
        *,
        terminal: bool = False,
    ) -> None:
        with self._condition:
            sequence = self._next_sequence[run_id]
            self._next_sequence[run_id] += 1
            events = self._events[run_id]
            events.append(RunProgressEvent(sequence, event_type, data))
            if len(events) > self._max_events:
                del events[: len(events) - self._max_events]
            if terminal:
                self._terminal_runs.add(run_id)
            self._condition.notify_all()

    def has_events(self, run_id: int) -> bool:
        with self._condition:
            return bool(self._events.get(run_id))

    def stream(
        self,
        run_id: int,
        *,
        after_sequence: int = 0,
        heartbeat_seconds: float = 15,
    ) -> Iterator[str]:
        cursor = after_sequence
        while True:
            deadline = monotonic() + heartbeat_seconds
            with self._condition:
                pending = [
                    event for event in self._events.get(run_id, [])
                    if event.sequence > cursor
                ]
                while not pending and run_id not in self._terminal_runs:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
                    pending = [
                        event for event in self._events.get(run_id, [])
                        if event.sequence > cursor
                    ]
                terminal = run_id in self._terminal_runs

            if not pending:
                if terminal:
                    return
                yield ": heartbeat\n\n"
                continue
            for event in pending:
                cursor = event.sequence
                payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {payload}\n\n"
            if terminal:
                return
