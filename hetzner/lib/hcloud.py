"""A small Hetzner Cloud client for "one VM per project, created when needed, deleted when idle" (standard library only).

    hz = Client(token)
    server, how = ensure_server(hz, name="ci-myproject", selector="project=myproject", labels={...},
                                places=[("cx33", "fsn1"), ("cx33", "nbg1")], image="ubuntu-24.04",
                                ssh_key_ids=[hz.ensure_ssh_key("ci-myproject", public_key)], user_data=cloud_init)

`ensure_server` always converges on the same VM: it reuses a running one, powers on a stopped one, waits out
transient states, creates one otherwise (Hetzner rejects a second server with the same name, so two concurrent
callers end on one VM), and tries the next (type, location) when Hetzner has no capacity. Host keys are pinned with
`hostkey_fingerprint` (ssh-keyscan); `Ssh` runs commands with a known_hosts file and no prompts.
"""
import base64
import hashlib
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.hetzner.cloud/v1"
RETRY = (429, 500, 502, 503, 504)


class HcloudError(Exception):
    pass


class ApiError(HcloudError):
    def __init__(self, status, code, message):
        super().__init__("Hetzner API %s %s : %s" % (status, code, message))
        self.status, self.code = status, code


class Client:
    def __init__(self, token, api=API, sleep=5.0):
        self.token, self.api, self.sleep = token, api, sleep

    def req(self, method, path, body=None, retries=4):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries + 1):
            r = urllib.request.Request(self.api + path, data=data, method=method, headers={
                "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(r, timeout=60) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                raw = e.read()
                try:
                    err = json.loads(raw).get("error", {})
                except ValueError:
                    err = {}
                if e.code in RETRY and attempt < retries:
                    time.sleep(min(2 ** attempt, 20) * (self.sleep / 5 or 0.01))
                    continue
                raise ApiError(e.code, err.get("code", "?"), err.get("message", raw[:200]))
            except urllib.error.URLError as e:
                if attempt < retries:
                    time.sleep(2 ** attempt * (self.sleep / 5 or 0.01))
                    continue
                raise HcloudError("Hetzner API unreachable: %s" % e)

    def servers(self, selector):
        """Servers matching a label selector (e.g. "project=x"), oldest first."""
        q = urllib.parse.quote(selector)
        return sorted(self.req("GET", "/servers?label_selector=%s&per_page=50" % q).get("servers", []), key=lambda s: s["id"])

    def get_server(self, sid):
        try:
            return self.req("GET", "/servers/%d" % sid)["server"]
        except ApiError as e:
            if e.status == 404:
                return None
            raise

    def set_labels(self, sid, labels):
        self.req("PUT", "/servers/%d" % sid, {"labels": labels})

    def delete_server(self, sid):
        self.req("DELETE", "/servers/%d" % sid)

    def ssh_keys(self):
        return self.req("GET", "/ssh_keys?per_page=50").get("ssh_keys", [])

    def delete_ssh_key(self, kid):
        self.req("DELETE", "/ssh_keys/%d" % kid)

    def ensure_ssh_key(self, name, public_key):
        """The id of the Hetzner SSH key holding public_key, created under name (or name-<hash>) when missing."""
        blob = public_key.split()[1]
        for k in self.ssh_keys():
            if blob in k["public_key"]:
                return k["id"]
        try:
            return self.req("POST", "/ssh_keys", {"name": name, "public_key": public_key})["ssh_key"]["id"]
        except ApiError as e:
            if e.code == "uniqueness_error":
                return self.ensure_ssh_key(name + "-" + hashlib.sha1(blob.encode()).hexdigest()[:6], public_key)
            raise


def sanitize_label(v):
    """A value Hetzner accepts as a label key or value."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(v))[:63]


def ensure_server(hz, name, selector, labels, places, image, ssh_key_ids, user_data, log=print, timeout=900,
                  no_capacity_hint=""):
    """Find/reuse, power on or create the one server matching selector. -> (server, "reused"|"restarted"|"created")."""
    how, powered_on, places = None, False, list(places)
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = hz.servers(selector)
        if len(found) > 1:
            log("WARNING: %d servers match %s; the oldest one is used" % (len(found), selector))
        s = found[0] if found else None
        if s is None:
            if not places:
                raise HcloudError("Hetzner has no capacity for the requested server types and locations right now "
                                  "(resource_unavailable). Retry later%s." % no_capacity_hint)
            stype, loc = places[0]
            body = {"name": name, "server_type": stype, "image": image, "location": loc, "ssh_keys": list(ssh_key_ids),
                    "labels": labels, "user_data": user_data, "start_after_create": True}
            try:
                hz.req("POST", "/servers", body)
                how = how or "created"
                log("VM created: %s (%s, %s)" % (name, stype, loc))
            except ApiError as e:
                if e.code in ("resource_unavailable", "placement_error") or (e.status == 412 and "placement" in str(e)):
                    places.pop(0)
                    log("no Hetzner capacity for %s in %s: trying %s"
                        % (stype, loc, "%s in %s" % places[0] if places else "nothing else"))
                    continue
                if e.code == "uniqueness_error":
                    log("VM already created by another caller: converging")
                else:
                    raise
            time.sleep(hz.sleep)
            continue
        st = s["status"]
        if st == "running":
            return s, how or "reused"
        if st == "off":
            if not powered_on:
                hz.req("POST", "/servers/%d/actions/poweron" % s["id"])
                powered_on = True
                how = how or "restarted"
                log("VM is off: power-on requested")
        elif st == "deleting":
            log("VM is being deleted: waiting, then recreating")
        elif st == "stopping":
            powered_on = False       # power it on once it is `off`
        time.sleep(hz.sleep)
    raise HcloudError("the VM did not reach `running` in time")


def std_fingerprint(b64_blob):
    """The fingerprint SSH apps display: SHA256:<base64 without padding>."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(b64_blob)).digest()).decode().rstrip("=")


def hostkey_fingerprint(host, runner=None):
    """-> (known_hosts line, short hex fingerprint for a label, SHA256: fingerprint), or (None, None, None)."""
    run = runner or (lambda c: subprocess.run(c, text=True, capture_output=True))
    p = run(["ssh-keyscan", "-t", "ed25519", "-T", "5", host])
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "ssh-ed25519":
            blob = base64.b64decode(parts[2])
            return "%s ssh-ed25519 %s" % (host, parts[2]), hashlib.sha256(blob).hexdigest()[:48], std_fingerprint(parts[2])
    return None, None, None


class Ssh:
    """Commands on one host as one user, with a key file and a pinned known_hosts file; never prompts."""

    def __init__(self, key_file, host, known_hosts, user):
        self.base = ["ssh", "-i", str(key_file), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                     "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30",
                     "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=%s" % known_hosts,
                     "%s@%s" % (user, host)]

    def call(self, command, stdin_bytes=b"", stream=False):
        """-> (exit code, output lines); stdin_bytes is written while the output is read (no deadlock)."""
        p = subprocess.Popen(self.base + [command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        out = []

        def feed():
            try:
                p.stdin.write(stdin_bytes)
                p.stdin.close()
            except BrokenPipeError:
                pass
        t = threading.Thread(target=feed, daemon=True)
        t.start()
        for line in iter(p.stdout.readline, b""):
            s = line.decode(errors="replace").rstrip("\n")
            out.append(s)
            if stream:
                print("  | " + s, flush=True)
        p.wait()
        t.join(timeout=5)
        return p.returncode, out
