"""Shared fixtures for misuse tests: canary secrets, a folder guard, a hostile agent, and seeded fuzzing."""

from __future__ import annotations

import copy
import csv
import http.client
import io
import json
import os
import random
import secrets
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from quantarium import agents
from quantarium.agents import AgentSettings
from quantarium.discovery import announce
from quantarium.server import TOKEN_HEADER, QuantariumServer
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[2]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
CANARY_PREFIX = "BLCANARY"
# Environment variables a careless change could leak: model keys, broker keys, and tokens.
CANARY_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
              "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "GITHUB_TOKEN", "QUANTARIUM_TEST_KEY")
# Files in a home folder that hold sign-ins on a real computer; here they hold canaries.
CANARY_FILES = (".ssh/id_ed25519", ".aws/credentials", ".codex/auth.json", ".claude/.credentials.json",
                ".config/gh/hosts.yml", "AppData/Local/Google/Chrome/User Data/Default/Login Data")


class Canaries:
    """Fake secrets in the environment and in a fake home folder, and a way to find them again."""

    def __init__(self, base: Path) -> None:
        self.home = base / "home"
        self.values: dict[str, str] = {}
        for name in CANARY_ENV:
            self.values[f"env:{name}"] = f"{CANARY_PREFIX}-{name}-{secrets.token_hex(8)}"
        for relative in CANARY_FILES:
            value = f"{CANARY_PREFIX}-file-{secrets.token_hex(8)}"
            self.values[f"file:{relative}"] = value
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fake secret for tests\n{value}\n", encoding="utf-8")

    @contextmanager
    def active(self):
        """Point HOME and USERPROFILE at the fake home, and put canary keys in the environment."""
        environment = {"HOME": str(self.home), "USERPROFILE": str(self.home),
                       **{key.split(":", 1)[1]: value for key, value in self.values.items() if key.startswith("env:")}}
        with mock.patch.dict(os.environ, environment):
            yield self

    def found_in_text(self, *texts: object) -> list[str]:
        """The canaries that appear in any of these texts (or JSON-serializable values)."""
        blob = "\n".join(text if isinstance(text, str) else json.dumps(text, default=str) for text in texts)
        return sorted(name for name, value in self.values.items() if value in blob)

    def found_in_folder(self, folder: Path) -> list[str]:
        """The canaries that appear in any file under a folder, databases included."""
        hits = set()
        needles = {name: value.encode("utf-8") for name, value in self.values.items()}
        for path in Path(folder).rglob("*"):
            if not path.is_file() or self.home in path.parents:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            hits |= {f"{name} in {path.name}" for name, needle in needles.items() if needle in data}
        return sorted(hits)


LOOPBACK_NAMES = {"127.0.0.1", "::1", "localhost"}


class NetworkGuard:
    """Refuses every connection and name lookup in this process that is not loopback, and records it."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._patches = []

    def _host(self, address) -> str:
        return str(address[0] if isinstance(address, tuple) else address)

    def _check(self, host: str) -> None:
        if host not in LOOPBACK_NAMES and not host.startswith("127."):
            self.attempts.append(host)
            raise OSError(f"Network access outside loopback is blocked in tests: {host}")

    def __enter__(self) -> "NetworkGuard":
        connect, connect_ex, lookup = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
        guard = self

        def guarded_connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                guard._check(guard._host(address))
            return connect(sock, address)

        def guarded_connect_ex(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                guard._check(guard._host(address))
            return connect_ex(sock, address)

        def guarded_lookup(host, *args, **kwargs):
            if host is not None:
                guard._check(host.decode() if isinstance(host, bytes) else str(host))
            return lookup(host, *args, **kwargs)

        self._patches = [mock.patch.object(socket.socket, "connect", guarded_connect),
                         mock.patch.object(socket.socket, "connect_ex", guarded_connect_ex),
                         mock.patch.object(socket, "getaddrinfo", guarded_lookup)]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc) -> None:
        for patch in reversed(self._patches):
            patch.stop()


def fake_keyring() -> types.ModuleType:
    """A stand-in for the keyring package that keeps keys in memory, so no test reaches the real keychain."""
    module = types.ModuleType("keyring")
    module.store = {}
    module.get_password = lambda service, name: module.store.get((service, name))
    module.set_password = lambda service, name, value: module.store.__setitem__((service, name), value)
    module.delete_password = lambda service, name: module.store.pop((service, name), None)
    return module


class FolderGuard:
    """Notices any change under folders a test must not touch."""

    def __init__(self, *folders: Path) -> None:
        self.folders = [Path(folder) for folder in folders]
        self.before = self._snapshot()

    def _snapshot(self) -> dict[str, tuple[int, int]]:
        state = {}
        for folder in self.folders:
            if not folder.exists():
                continue
            for path in folder.rglob("*"):
                if "__pycache__" in path.parts:
                    continue
                stat = path.stat()
                state[str(path)] = (stat.st_size if path.is_file() else -1, stat.st_mtime_ns if path.is_file() else 0)
        return state

    def changes(self) -> list[str]:
        after = self._snapshot()
        changed = [f"added {path}" for path in after.keys() - self.before.keys()]
        changed += [f"removed {path}" for path in self.before.keys() - after.keys()]
        changed += [f"changed {path}" for path in after.keys() & self.before.keys() if after[path] != self.before[path]]
        return sorted(changed)


class MisuseCase(unittest.TestCase):
    """A Quantarium server on a disposable workspace, an agent with every tier, canaries, and a folder guard.

    Every test ends by checking that no canary leaked into the workspace, and
    that nothing outside the workspace changed: the fake home, a sentinel
    folder beside the workspace, and Quantarium's own source.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        base = Path(self.temp.name)
        self.root = base / "workspace"
        self.root.mkdir()
        self.outside = base / "outside"
        self.outside.mkdir()
        self.sentinel = self.outside / "sentinel.txt"
        self.canaries = Canaries(base)
        # The sentinel holds a canary, so a read that escapes the workspace shows up as a leak.
        outside_canary = f"{CANARY_PREFIX}-outside-{secrets.token_hex(8)}"
        self.canaries.values["file:outside/sentinel.txt"] = outside_canary
        self.sentinel.write_text(f"outside the workspace\n{outside_canary}\n", encoding="utf-8")
        self.environment = self.canaries.active()
        self.environment.__enter__()
        self.network = NetworkGuard().__enter__()
        # Only this one entry of sys.modules changes; patching the whole dict would unload modules on exit.
        self.real_keyring = sys.modules.get("keyring")
        self.keyring = sys.modules["keyring"] = fake_keyring()
        self.guard = FolderGuard(self.outside, self.canaries.home, ROOT / "quantarium", ROOT / "examples")
        self.server = QuantariumServer(Workspace(self.root), port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        announce(self.root, self.server.url)
        self.api = self.server.api
        AgentSettings(self.api.workspace).enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
        self.token = self.api.agents.create_connection("Hostile agent", tiers=list(agents.TIERS),
                                                       edit_user_scripts=True)["token"]
        self.responses: list[object] = []

    def wait_for_jobs(self, limit: float = 120) -> None:
        """Let queued jobs finish, so their results are checked and the workspace can be removed."""
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            with self.api._lock:
                busy = [job for job in self.api.jobs.values() if job["status"] in {"queued", "running"}]
            if not busy:
                return
            time.sleep(0.05)
        self.fail(f"{len(busy)} jobs still running after {limit} seconds")

    def tearDown(self) -> None:
        try:
            self.wait_for_jobs()
            with self.api._lock:
                self.responses.append([dict(job) for job in self.api.jobs.values()])
            self.server.shutdown()
            self.server.server_close()
            self.assertEqual(self.canaries.found_in_text(*self.responses), [], "a canary reached a response")
            self.assertEqual(self.canaries.found_in_folder(self.root), [], "a canary was written in the workspace")
            self.assertEqual(self.guard.changes(), [], "something outside the workspace changed")
            self.assertEqual(self.network.attempts, [], "something tried to reach the network")
        finally:
            if self.real_keyring is None:
                sys.modules.pop("keyring", None)
            else:
                sys.modules["keyring"] = self.real_keyring
            self.network.__exit__(None, None, None)
            self.environment.__exit__(None, None, None)
            self.temp.cleanup()

    def call(self, method: str, path: str, body: object = None, *, token: str | None = None,
             headers: dict | None = None, raw: bytes | None = None) -> tuple[int, object]:
        """One HTTP request to the test server. ``token`` sends it as an agent; otherwise as the browser."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        sent = dict(headers or {})
        payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        if payload is not None:
            sent.setdefault("Content-Type", "application/json")
            if token is None:
                sent.setdefault(TOKEN_HEADER, self.server.token)
        if token is not None:
            sent["Authorization"] = f"Bearer {token}"
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        text = response.read().decode("utf-8", errors="replace")
        connection.close()
        try:
            data = json.loads(text)
        except ValueError:
            data = text
        self.responses.append(data)
        return response.status, data

    def agent(self, kind: str, name: str, params: dict | None = None) -> tuple[int, object]:
        """An agent request: a GET route, a command, or a job."""
        if kind == "get":
            from urllib.parse import urlencode

            query = f"?{urlencode(params)}" if params else ""
            return self.call("GET", f"/api/v1/{name}{query}", token=self.token)
        endpoint, field = ("commands", "command") if kind == "command" else ("jobs", "action")
        return self.call("POST", f"/api/v1/{endpoint}", {field: name, "params": params or {}}, token=self.token)


def hostile_strings(outside_file: Path) -> list[str]:
    """Strings that commonly break input handling: paths that leave a folder, hidden folders, odd text."""
    return ["", " ", ".", "..", "../", "../../", "../outside/sentinel.txt", str(outside_file),
            str(outside_file).replace("\\", "/"), ".quantarium", ".quantarium/agents.json", "scenarios/../.quantarium",
            "scenarios/..\\..\\outside", "%2e%2e/%2e%2e/", "~", "~/.ssh/id_ed25519", "con", "nul", "aux.txt",
            "a" * 5000, "\x00", "line\nbreak", "tab\tseparated", "‮evil", "üñíçødé", "<b>markup</b>",
            '{"json": "inside"}', "' or '1'='1", "-1", "1e309", "NaN"]


class Fuzzer:
    """Seeded random values for parameters: every failure can be replayed from its seed."""

    def __init__(self, seed: int, outside_file: Path) -> None:
        self.random = random.Random(seed)
        self.strings = hostile_strings(outside_file)

    def value(self, depth: int = 0) -> object:
        roll = self.random.random()
        if roll < 0.45:
            return self.random.choice(self.strings)
        if roll < 0.6:
            return self.random.choice([0, -1, 1, 2 ** 63, -(2 ** 63), 10 ** 20, 0.5, -1e308, 1e-12])
        if roll < 0.7:
            return self.random.choice([True, False, None])
        if depth >= 2:
            return self.random.choice(self.strings)
        if roll < 0.85:
            return [self.value(depth + 1) for _ in range(self.random.randint(0, 3))]
        return {self.random.choice(self.strings[:12] + ["id", "key", "spec", "name"]): self.value(depth + 1)
                for _ in range(self.random.randint(0, 3))}

    def params(self, keys: list[str]) -> dict:
        """Parameters with some known keys given hostile values, sometimes with extra keys."""
        chosen = {key: self.value() for key in keys if self.random.random() < 0.8}
        if self.random.random() < 0.3:
            chosen[self.random.choice(self.strings[:12])] = self.value()
        return chosen


def mutate_json(value: object, fuzzer: Fuzzer) -> object:
    """A copy with one random node replaced, removed, or wrapped."""
    value = copy.deepcopy(value)
    nodes = []

    def walk(node, parent, key):
        nodes.append((parent, key))
        if isinstance(node, dict):
            for child_key, child in node.items():
                walk(child, node, child_key)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, node, index)

    walk(value, None, None)
    parent, key = fuzzer.random.choice(nodes)
    if parent is None:
        return fuzzer.value()
    roll = fuzzer.random.random()
    if roll < 0.2 and isinstance(parent, dict):
        del parent[key]
    elif roll < 0.3:
        parent[key] = [parent[key]]
    else:
        parent[key] = fuzzer.value()
    return value


def mutate_csv(text: str, fuzzer: Fuzzer) -> str:
    """A copy with rows dropped, duplicated, shortened, lengthened, or filled with hostile values."""
    rows = list(csv.reader(io.StringIO(text)))
    for _ in range(fuzzer.random.randint(1, 3)):
        index = fuzzer.random.randrange(len(rows))
        roll = fuzzer.random.random()
        if roll < 0.15:
            rows.pop(index)
        elif roll < 0.3:
            rows.insert(index, list(rows[index]))
        elif roll < 0.45:
            rows[index] = rows[index][:fuzzer.random.randrange(len(rows[index]) + 1)]
        elif roll < 0.55:
            rows[index] = rows[index] + [str(fuzzer.value())]
        else:
            row = rows[index]
            if row:
                column = fuzzer.random.randrange(len(row))
                row[column] = fuzzer.random.choice([str(fuzzer.value()), "nan", "inf", "-inf", "-1", "0", "1e309",
                                                    "", " ", "2030-02-30", "9999-12-31", "1,5", "\x00"])
        if not rows:
            rows = [[]]
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows(rows)
    result = output.getvalue()
    if fuzzer.random.random() < 0.1:
        result = result[:fuzzer.random.randrange(len(result) + 1)]
    return result
