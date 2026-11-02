"""
Direct-mode test harness for the FlightAttest contract.

Runs the real contract file with a fake `genlayer` module:
- storage shims (TreeMap / DynArray / Address / u32, lazy contract fields),
- a scripted web (gl.nondet.web.get) per consensus node,
- a deterministic rule-based "LLM" oracle per node (gl.nondet.exec_prompt)
  driven by `FA_OBS{...}` sentinel lines embedded in test pages,
- a gl.vm.run_nondet_unsafe simulator that executes the leader closure and
  then replays the validator closure on THREE independent validator nodes.

This is an independent oracle: the fake model derives its answers from the
page it is shown, exactly like the real model is supposed to. Diverging pages
between nodes therefore produce consensus failures, which the tests assert.
"""

import importlib.util
import json
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import get_args, get_origin

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "flight_attest.py"

# --------------------------------------------------------------------------- #
# Fake genlayer runtime                                                        #
# --------------------------------------------------------------------------- #


class UserError(Exception):
    """Stands in for gl.vm.UserError (contract input errors)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ConsensusError(Exception):
    """Raised by the simulated consensus when any validator disagrees.

    On a real network the transaction would be undetermined and nothing would
    be stored; tests assert both the failure and the absence of state changes.
    """


class Return:
    """Stands in for gl.vm.Return (the leader's accepted output wrapper)."""

    def __init__(self, calldata):
        self.calldata = calldata


class Address:
    def __init__(self, hex_value: str):
        self._hex = hex_value

    @property
    def as_hex(self) -> str:
        return self._hex

    def __eq__(self, other):
        return isinstance(other, Address) and other._hex == self._hex

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self._hex)

    def __repr__(self):
        return "Address(" + self._hex + ")"


class u32(int):
    def __new__(cls, value=0):
        value = int(value)
        if value < 0 or value > 2**32 - 1:
            raise ValueError("u32 overflow")
        return super().__new__(cls, value)


class u256(int):
    def __new__(cls, value=0):
        value = int(value)
        if value < 0 or value > 2**256 - 1:
            raise ValueError("u256 overflow")
        return super().__new__(cls, value)


class DynArray(list):
    pass


def _zero_value(tp):
    origin = get_origin(tp)
    if tp is str:
        return ""
    if tp is bool:
        return False
    if tp is Address:
        return Address("0x")
    if origin is DynArray or tp is DynArray:
        return DynArray()
    if isinstance(tp, type) and issubclass(tp, int):
        return tp(0)
    raise TypeError("no zero value for " + repr(tp))


def _default_instance(tp):
    obj = tp.__new__(tp)
    for field_name, field_type in tp.__annotations__.items():
        object.__setattr__(obj, field_name, _zero_value(field_type))
    return obj


class TreeMap(dict):
    def __init__(self, value_type=None):
        super().__init__()
        self._value_type = value_type

    def __class_getitem__(cls, item):
        import types as _t

        return _t.GenericAlias(cls, item)

    def get_or_insert_default(self, key):
        if key not in self:
            if self._value_type is None:
                raise TypeError("untyped TreeMap")
            self[key] = _default_instance(self._value_type)
        return self[key]


def allow_storage(cls):
    return cls


class Contract:
    """Base class: materializes annotated storage fields lazily, like GenVM."""

    def __getattr__(self, name):
        annotations = type(self).__annotations__
        if name in annotations:
            tp = annotations[name]
            if get_origin(tp) is TreeMap or tp is TreeMap:
                args = get_args(tp)
                instance = TreeMap(args[1] if len(args) > 1 else None)
                object.__setattr__(self, name, instance)
                return instance
        raise AttributeError(name)


# --------------------------------------------------------------------------- #
# Scripted non-deterministic world                                             #
# --------------------------------------------------------------------------- #


@dataclass
class Page:
    status: int          # HTTP status; 0 means "network failure" (raises)
    body: bytes


def ok_page(html_text: str) -> Page:
    return Page(200, html_text.encode("utf-8"))


def http_error(status: int) -> Page:
    if status == 200:
        raise ValueError("http_error cannot be 200")
    return Page(status, b"<html><body>error</body></html>")


def net_fail() -> Page:
    return Page(0, b"")


def raw_body(data: bytes, status: int = 200) -> Page:
    return Page(status, data)


DEFAULT_EXTRACTION = {
    "identity": "UNCLEAR",
    "status": "NONE",
    "new_dep_date": "",
    "new_dep_time": "",
    "new_dep_tz": "",
    "diversion_airport": "",
    "excerpt": "",
}


class Node:
    """One consensus node's scripted environment."""

    def __init__(self, name: str):
        self.name = name
        self.pages = {}            # url -> Page
        self.llm_mode = "oracle"   # oracle | raw_string | fenced | trash | raise
        self.llm_seed = None       # optional dict overriding the parsed sentinel

    def web_get(self, url: str) -> Page:
        page = self.pages.get(url)
        if page is None:
            raise AssertionError(self.name + " fetched an unscripted url: " + url)
        if page.status == 0:
            raise ConnectionError("scripted network failure")
        return page

    def exec_prompt(self, prompt: str, response_format=None):
        if self.llm_mode == "raise":
            raise RuntimeError("scripted model failure")
        result = self._oracle(prompt)
        if self.llm_mode == "trash":
            return "I cannot help with that."
        if self.llm_mode == "fenced":
            return "```json\n" + json.dumps(result) + "\n```"
        if self.llm_mode == "raw_string":
            return json.dumps(result)
        return result

    def _oracle(self, prompt: str) -> dict:
        match = re.search(r"<<<PAGE\n(.*)\nPAGE>>>", prompt, re.DOTALL)
        page = match.group(1) if match else ""
        sentinel = re.search(r"FA_OBS(\{[^{}]*\})", page)
        result = dict(DEFAULT_EXTRACTION)
        if sentinel is not None:
            observed = json.loads(sentinel.group(1))
            for key, value in observed.items():
                if value is None:
                    continue
                value = str(value)
                # Excerpt keys ("@ON_TIME", "@DELAYED", ...) resolve to natural
                # sentences that appear in the page body, NOT in the sentinel,
                # so quote-substring checks stay meaningful.
                if key == "excerpt" and value.startswith("@"):
                    value = QUOTES[value[1:]]
                result[key] = value
        if self.llm_seed is not None:
            result.update(self.llm_seed)
        return result


NODES = {"leader": Node("leader"), "v1": Node("v1"), "v2": Node("v2"), "v3": Node("v3")}
ACTIVE = {"node": None}

DEFAULT_SENDER = Address("0x9a7c21d54f0e4b8c8d8aa1f2b3c4d5e6f708192a")
SECOND_SENDER = Address("0x1111111111111111111111111111111111111111")
DEFAULT_TIME = "2026-03-01T10:00:00Z"


def _web_get(url: str):
    return ACTIVE["node"].web_get(url)


def _exec_prompt(prompt: str, response_format=None):
    return ACTIVE["node"].exec_prompt(prompt, response_format=response_format)


def _run_nondet_unsafe(leader_fn, validator_fn):
    ACTIVE["node"] = NODES["leader"]
    try:
        leader_result = Return(leader_fn())
    except UserError as error:
        leader_result = UserError(error.message)
    finally:
        ACTIVE["node"] = None
    for name in ("v1", "v2", "v3"):
        ACTIVE["node"] = NODES[name]
        try:
            agreed = validator_fn(leader_result)
        except Exception:
            agreed = False
        finally:
            ACTIVE["node"] = None
        if not agreed:
            raise ConsensusError("validator " + name + " rejected the leader result")
    return leader_result.calldata


class _PublicNS:
    @staticmethod
    def write(fn):
        return fn

    @staticmethod
    def view(fn):
        return fn


class _MessageNS:
    sender_address = DEFAULT_SENDER


class _VMNS:
    UserError = UserError
    Return = Return
    run_nondet_unsafe = staticmethod(_run_nondet_unsafe)


class _NondetWebNS:
    get = staticmethod(_web_get)


class _NondetNS:
    web = _NondetWebNS()
    exec_prompt = staticmethod(_exec_prompt)


class _GL:
    Contract = Contract
    public = _PublicNS()
    message = _MessageNS()
    message_raw = {"datetime": DEFAULT_TIME}
    nondet = _NondetNS()
    vm = _VMNS()


def _fake_genlayer_module():
    module = types.ModuleType("genlayer")
    module.__all__ = ["gl", "TreeMap", "DynArray", "Address", "u32", "u256", "allow_storage"]
    module.gl = _GL()
    module.TreeMap = TreeMap
    module.DynArray = DynArray
    module.Address = Address
    module.u32 = u32
    module.u256 = u256
    module.allow_storage = allow_storage
    return module


sys.modules["genlayer"] = _fake_genlayer_module()
GL = sys.modules["genlayer"].gl


def load_contract_module():
    spec = importlib.util.spec_from_file_location("flight_attest", CONTRACT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def new_contract():
    module = load_contract_module()
    return module, module.FlightAttest()


def set_sender(address: Address = DEFAULT_SENDER):
    GL.message.sender_address = address


def set_time(iso: str = DEFAULT_TIME):
    GL.message_raw["datetime"] = iso


def reset_environment():
    for node in NODES.values():
        node.pages = {}
        node.llm_mode = "oracle"
        node.llm_seed = None
    set_sender(DEFAULT_SENDER)
    set_time(DEFAULT_TIME)


def script_pages(mapping, nodes=None):
    """Give the same pages to the leader and every validator."""
    for name in (nodes or ("leader", "v1", "v2", "v3")):
        NODES[name].pages.update(mapping)


# --------------------------------------------------------------------------- #
# Test page builders                                                           #
# --------------------------------------------------------------------------- #

AIRLINE_URL = "https://ops.skyline-air.aero/flights/ST221"
DEP_PORT_URL = "https://status.sgn-airport.aero/departures/ST221"
ARR_PORT_URL = "https://boards.sfo-airport.aero/arrivals/ST221"
TRACKER_URL = "https://track.flightradar.io/live/ST221"

STANDARD_URLS = [AIRLINE_URL, DEP_PORT_URL, TRACKER_URL]
STANDARD_ROLES = ["AIRLINE", "DEPARTURE_AIRPORT", "TRACKER"]

REGISTER_ARGS = {
    "flight_id": "st221-2026-03-10",
    "carrier": "Skyline Air",
    "flight_number": "ST221",
    "departure_airport": "SGN",
    "arrival_airport": "SFO",
    "scheduled_departure": "2026-03-10T08:30:00Z",
    "source_urls": STANDARD_URLS,
    "source_roles": STANDARD_ROLES,
    "supersedes": "",
}

QUOTE_ON_TIME = "Flight ST221 is on schedule and boarding at gate B7."
QUOTE_DELAYED = "Flight ST221 now departs at 11:45 UTC due to late inbound aircraft."
QUOTE_DIVERTED = "Flight ST221 is diverting and will land at OAK instead of SFO."
QUOTE_CANCELLED = "Flight ST221 has been cancelled and all passengers rebooked."
QUOTE_LANDED = "Flight ST221 touched down in San Francisco at 14:02 UTC."

QUOTES = {
    "ON_TIME": QUOTE_ON_TIME,
    "DELAYED": QUOTE_DELAYED,
    "DIVERTED": QUOTE_DIVERTED,
    "CANCELLED": QUOTE_CANCELLED,
    "LANDED": QUOTE_LANDED,
}


def make_obs(identity="MATCH", status="NONE", new_dep_date="", new_dep_time="",
             new_dep_tz="", diversion_airport="", excerpt=""):
    payload = {
        "identity": identity,
        "status": status,
        "new_dep_date": new_dep_date or None,
        "new_dep_time": new_dep_time or None,
        "new_dep_tz": new_dep_tz or None,
        "diversion_airport": diversion_airport or None,
        "excerpt": excerpt or None,
    }
    return "FA_OBS" + json.dumps(payload, separators=(",", ":"))


def make_page(headline: str, obs: str, extra: str = "") -> str:
    return (
        "<!doctype html><html><head><title>Skyline Air ST221</title>"
        "<style>.hidden{display:none}</style>"
        "<script>window.tracking = true;</script></head>"
        "<body><main><h1>Skyline Air &mdash; flight ST221, SGN to SFO</h1>"
        "<p>" + headline + "</p>"
        "<p class=\"obs\">" + obs + "</p>"
        + extra +
        "</main></body></html>"
    )


def agree_pages(pages_by_url):
    """Script identical pages on every node (leader + 3 validators)."""
    script_pages(pages_by_url)
    return pages_by_url


def register_standard(contract, **overrides):
    args = dict(REGISTER_ARGS)
    args.update(overrides)
    contract.register_flight(**args)
    return args


def expect_user_error(fn, needle: str):
    try:
        fn()
    except UserError as error:
        assert needle in error.message, str(error.message)
        return error
    raise AssertionError("expected UserError containing: " + needle)


def expect_consensus_error(fn):
    try:
        fn()
    except ConsensusError:
        return
    raise AssertionError("expected ConsensusError")
