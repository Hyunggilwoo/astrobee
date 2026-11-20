"""Shared front end for typed (cli.py) and spoken (voice_input.py) commands.

Text -> IntentParser -> optional confirmation -> FIFO queue -> worker thread
-> Sequencer. "stop" (typed, spoken, or Ctrl-C) never waits in the queue: it
clears pending requests and sends stopAllMotion immediately, even while a
move is in flight.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, List, Optional

import astrobee_cmd as ac
from intent_parser import Intent, IntentParser, ParseError
from sequencer import SequenceResult, Sequencer

log = logging.getLogger("crew_assist.session")

ConfirmFn = Callable[[Intent], bool]


class CrewSession:
    def __init__(self, parser: IntentParser, sequencer: Sequencer,
                 commander: ac.AstrobeeCommander, confirm: Optional[ConfirmFn] = None,
                 echo: Callable[[str], None] = print):
        self.parser = parser
        self.seq = sequencer
        self.cmd = commander
        self.confirm = confirm
        self.echo = echo
        self.results: List[SequenceResult] = []
        self._q: "queue.Queue[Optional[Intent]]" = queue.Queue()
        self._busy = threading.Event()
        self._worker = threading.Thread(target=self._loop, name="crew-worker", daemon=True)
        self._worker.start()

    # -- worker ---------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            intent = self._q.get()
            if intent is None:
                self._q.task_done()
                return
            self._busy.set()
            try:
                r = self.seq.execute(intent)
                self.results.append(r)
                self.echo("  " + r.summary())
            except Exception as e:  # never let the worker die silently
                log.exception("sequence crashed: %s", e)
            finally:
                self._busy.clear()
                self._q.task_done()

    # -- API --------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy.is_set() or not self._q.empty()

    def stop(self, why: str = "stop") -> ac.AckResult:
        dropped = 0
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            self._q.task_done()
            if item is None:          # keep a pending shutdown request
                self._q.put(None)
                break
            dropped += 1
        self.echo("! %s -> stopAllMotion%s" % (why, " (dropped %d queued)" % dropped if dropped else ""))
        res = self.cmd.stop()
        self.echo("  stopAllMotion: %s %s" % (res.name, res.message))
        return res

    def submit(self, text: str) -> Optional[Intent]:
        """Parse and queue (or immediately stop). Returns the intent or None."""
        try:
            intent = self.parser.parse(text)
        except ParseError as e:
            self.echo("? %s" % e)
            return None
        self.echo("> %s %s" % (intent.action, intent.target or ""))
        if intent.action == "stop":
            self.stop("stop requested")
            return intent
        if self.confirm is not None and not self.confirm(intent):
            self.echo("  skipped")
            return None
        if self.busy:
            self.echo("  queued (robot busy; say 'stop' to cancel)")
        self._q.put(intent)
        return intent

    def wait_idle(self, poll_s: float = 0.2) -> None:
        """Block until the queue drains. Raises KeyboardInterrupt to caller."""
        while self.busy:
            self._worker.join(poll_s)

    def close(self) -> None:
        self._q.put(None)
        self._worker.join(2.0)
