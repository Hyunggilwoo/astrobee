"""Rule-based text -> intent. No ML; unknown phrasing is rejected, not guessed.

Intent = {"action": one of ACTIONS, "target": canonical location name or None}
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

ACTIONS = ("undock", "go_to", "photograph", "return_to_dock", "stop")


@dataclass
class Intent:
    action: str
    target: Optional[str] = None
    text: str = ""


class ParseError(ValueError):
    pass


_FILLER = re.compile(
    r"\b(please|astrobee|robot|bee|now|then|could you|can you|would you|"
    r"go ahead and|hey|ok|okay)\b"
)
_STOP = re.compile(
    r"^(stop|halt|abort|freeze|cancel|stop all motion|emergency stop|e stop|estop|"
    r"stop moving|hold position)$"
)
_UNDOCK = re.compile(r"^(undock|un dock|leave (the )?dock|depart (the )?dock|launch)$")
_RETURN = re.compile(
    r"^(return (to )?(the )?(dock|home|berth( ?1| one)?)|go (back )?(to )?"
    r"(the )?(dock|home|berth( ?1| one)?)|go back home|dock|redock|re dock|"
    r"return home|come home|come back)$"
)
_PHOTO = re.compile(
    r"^(photograph|photo|image|snap|shoot|capture|inspect|"
    r"take an? (photo|picture|image|snapshot) of|"
    r"get an? (photo|picture|image) of)\s+(?P<t>.+)$"
)
_GOTO = re.compile(
    r"^(go|move|fly|navigate|head|proceed)( over)? (to|towards?)\s+(?P<t>.+)$"
)
_DEICTIC = re.compile(r"^(this|that|here|there|it)( (panel|one|thing|spot))?$")


def normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9\s-]", " ", t).replace("-", " ")
    t = _FILLER.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


class IntentParser:
    def __init__(self, locations: Dict[str, dict]):
        self.alias_to_name: Dict[str, str] = {}
        for name, loc in locations.items():
            for a in (
                [name, name.replace("_", " ")]
                + list(loc.get("aliases", []))
                + list(loc.get("spoken", []))
            ):
                self.alias_to_name[normalize(a)] = name

    def resolve(self, phrase: str) -> str:
        p = re.sub(r"^(the|a|an)\s+", "", normalize(phrase))
        if _DEICTIC.match(p):
            raise ParseError(
                "'%s' is ambiguous - name the location (known: %s)"
                % (phrase, ", ".join(self.known()))
            )
        if p in self.alias_to_name:
            return self.alias_to_name[p]
        raise ParseError(
            "unknown location '%s' (known: %s)" % (phrase, ", ".join(self.known()))
        )

    def known(self) -> List[str]:
        return sorted(set(self.alias_to_name.values()))

    def parse(self, text: str) -> Intent:
        t = normalize(text)
        if not t:
            raise ParseError("empty command")
        if _STOP.match(t):
            return Intent("stop", None, text)
        if _UNDOCK.match(t):
            return Intent("undock", None, text)
        if _RETURN.match(t):
            return Intent("return_to_dock", None, text)
        m = _PHOTO.match(t)
        if m:
            return Intent("photograph", self.resolve(m.group("t")), text)
        m = _GOTO.match(t)
        if m:
            target = self.resolve(m.group("t"))
            return Intent("go_to", target, text)
        raise ParseError(
            "not understood: '%s' (try: undock, go to <place>, photograph <place>, "
            "return to dock, stop)" % text
        )


def load_locations(path: str) -> Dict[str, dict]:
    import yaml

    with open(path) as f:
        doc = yaml.safe_load(f)
    locs = doc["locations"]
    for name, loc in locs.items():
        if len(loc["pos"]) != 3 or len(loc["quat"]) != 4:
            raise ValueError("bad pose for %s" % name)
    return locs
