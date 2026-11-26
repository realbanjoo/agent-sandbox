"""hetzner/hcloud: the client against a fake API, and ensure_server's convergence."""
import json, sys, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hetzner/lib"))
import hcloud  # noqa: E402

S = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body):
        b = json.dumps(body).encode(); self.send_response(code); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        S["auth"] = self.headers.get("Authorization")
        if self.path.startswith("/servers?"):
            S["flaky"] = S.get("flaky", 0) + 1
            if S["flaky"] == 1:
                return self.send(503, {"error": {"code": "unavailable"}})
            return self.send(200, {"servers": S.get("servers", [])})
        if self.path == "/servers/9":
            return self.send(404, {"error": {"code": "not_found", "message": "gone"}})
        if self.path.startswith("/ssh_keys"):
            return self.send(200, {"ssh_keys": [{"id": 3, "name": "k", "public_key": "ssh-ed25519 AAAAold x"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/ssh_keys":
            if body["name"] == "taken":
                return self.send(409, {"error": {"code": "uniqueness_error", "message": "name"}})
            return self.send(201, {"ssh_key": {"id": 4}})


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.hz = hcloud.Client("tok", api="http://127.0.0.1:%d" % cls.srv.server_port, sleep=0.01)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_retries_then_reads_with_the_token(self):
        S.update(servers=[{"id": 2}, {"id": 1}], flaky=0)
        self.assertEqual([s["id"] for s in self.hz.servers("project=x")], [1, 2])        # oldest first, after one 503
        self.assertEqual(S["auth"], "Bearer tok")
        self.assertIsNone(self.hz.get_server(9))

    def test_ssh_keys_are_reused_or_created_under_a_free_name(self):
        self.assertEqual(self.hz.ensure_ssh_key("k", "ssh-ed25519 AAAAold y"), 3)
        self.assertEqual(self.hz.ensure_ssh_key("taken", "ssh-ed25519 AAAAnew y"), 4)


class FakeHz:
    sleep = 0

    def __init__(self, states, full=0):
        self.states, self.full, self.calls = list(states), full, []

    def servers(self, selector):
        st = self.states.pop(0) if self.states else "running"
        return [] if st is None else [{"id": 1, "status": st}]

    def req(self, method, path, body=None):
        self.calls.append((method, path, body and (body["server_type"], body["location"])))
        if method == "POST" and path == "/servers" and self.full:
            self.full -= 1
            raise hcloud.ApiError(412, "resource_unavailable", "error during placement")
        return {}


class Ensure(unittest.TestCase):
    def ensure(self, hz, places=(("cx33", "fsn1"), ("cx33", "nbg1"))):
        return hcloud.ensure_server(hz, "ci-x", "project=x", {"project": "x"}, places, "ubuntu-24.04", [1], "#cloud-config",
                                    log=lambda m: None)

    def test_reuses_restarts_creates_and_falls_back(self):
        self.assertEqual(self.ensure(FakeHz(["running"]))[1], "reused")
        hz = FakeHz(["off", "starting", "running"])
        self.assertEqual(self.ensure(hz)[1], "restarted"); self.assertEqual(hz.calls, [("POST", "/servers/1/actions/poweron", None)])
        hz = FakeHz([None, None, "initializing", "running"], full=1)
        self.assertEqual(self.ensure(hz)[1], "created")
        self.assertEqual([c[2] for c in hz.calls], [("cx33", "fsn1"), ("cx33", "nbg1")])

    def test_no_capacity_anywhere(self):
        with self.assertRaises(hcloud.HcloudError) as e:
            self.ensure(FakeHz([None] * 5, full=2))
        self.assertIn("no capacity", str(e.exception))

    def test_labels_and_fingerprints(self):
        self.assertEqual(hcloud.sanitize_label("o/r name"), "o-r-name")
        import base64
        blob = base64.b64encode(b"key").decode()
        line, short, std = hcloud.hostkey_fingerprint("h", runner=lambda c: type("P", (), {"stdout": "h ssh-ed25519 %s\n" % blob})())
        self.assertEqual(line, "h ssh-ed25519 %s" % blob); self.assertEqual(len(short), 48); self.assertTrue(std.startswith("SHA256:"))


if __name__ == "__main__":
    unittest.main()
