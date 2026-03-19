"""
Monero Stratum protocol client for pool mining.
Handles login, job dispatch, share submission, TLS, and keepalive.
"""
import json
import socket
import ssl
import threading
import time
import logging

log = logging.getLogger(__name__)


class Job:
    """Single mining job received from the pool."""
    __slots__ = ("job_id", "blob", "target", "seed_hash", "height", "algo")

    def __init__(self, data: dict):
        self.job_id   = data["job_id"]
        self.blob     = bytes.fromhex(data["blob"])
        self.target   = data["target"]
        self.seed_hash = bytes.fromhex(data.get("seed_hash", "0" * 64))
        self.height   = data.get("height", 0)
        self.algo     = data.get("algo", "rx/0")

    @property
    def target_difficulty(self) -> int:
        t = self.target
        if len(t) <= 8:
            v = int.from_bytes(bytes.fromhex(t), "little")
            return (2**32 // v) if v else 0
        v = int.from_bytes(bytes.fromhex(t), "little")
        return (2**64 // v) if v else 0

    @property
    def target_value(self) -> int:
        """Expand compact target to a 256-bit threshold for hash comparison."""
        raw = bytes.fromhex(self.target)
        padded = raw + b"\xff" * (32 - len(raw))
        return int.from_bytes(padded, "little")


class StratumClient:
    """JSON-RPC stratum client for Monero pools."""

    def __init__(self, url: str, user: str, password: str = "x",
                 tls: bool = False, user_agent: str = "pyrx-miner/1.0",
                 tls_fingerprint: str = None):
        self.user = user
        self.password = password
        self.tls = tls
        self.tls_fingerprint = tls_fingerprint
        self.user_agent = user_agent or "pyrx-miner/1.0"

        self.session_id = None
        self.current_job: Job | None = None
        self._sock = None
        self._lock = threading.Lock()
        self._msg_id = 0
        self._connected = False
        self._running = False
        self._recv_thread = None
        self._job_cb = None
        self._accepted = 0
        self._rejected = 0

        self._parse_url(url)

    # ── connection ───────────────────────────────────────────────────────
    def _parse_url(self, url: str):
        for pfx in ("stratum+tcp://", "stratum+ssl://", "stratum+tls://"):
            url = url.replace(pfx, "")
        host, _, port = url.rpartition(":")
        if not host:
            host, port = url, "3333"
        self.host = host
        self.port = int(port)

    def connect(self):
        log.info(f"Connecting to {self.host}:{self.port}  TLS={self.tls}")
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(30)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self.tls:
            ctx = ssl.create_default_context()
            # Mining pools typically use self-signed certs — skip verification
            # (matches xmrig default behavior)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._sock = ctx.wrap_socket(raw, server_hostname=self.host)
        else:
            self._sock = raw
        self._sock.connect((self.host, self.port))
        self._connected = True
        log.info("Connected")

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _send(self, data: dict):
        msg = json.dumps(data) + "\n"
        with self._lock:
            self._sock.sendall(msg.encode())

    def _recv_line(self) -> str:
        buf = bytearray()
        while True:
            try:
                ch = self._sock.recv(1)
                if not ch:
                    raise ConnectionError("Connection closed by pool")
                buf += ch
                if ch == b"\n":
                    return buf.decode().strip()
            except socket.timeout:
                continue

    # ── RPC methods ──────────────────────────────────────────────────────
    def login(self) -> Job:
        mid = self._next_id()
        self._send({
            "id": mid, "jsonrpc": "2.0", "method": "login",
            "params": {
                "login": self.user, "pass": self.password,
                "agent": self.user_agent, "algo": ["rx/0"],
            },
        })
        resp = json.loads(self._recv_line())
        if resp.get("error"):
            raise ConnectionError(f"Login failed: {resp['error']}")
        result = resp.get("result", {})
        self.session_id = result.get("id")
        jd = result.get("job")
        if jd:
            self.current_job = Job(jd)
            log.info(
                f"Logged in  job={self.current_job.job_id}  "
                f"h={self.current_job.height}  diff={self.current_job.target_difficulty}"
            )
        return self.current_job

    def submit(self, job_id: str, nonce: str, result_hash: str):
        if not self._connected:
            return
        try:
            self._send({
                "id": self._next_id(), "jsonrpc": "2.0", "method": "submit",
                "params": {
                    "id": self.session_id, "job_id": job_id,
                    "nonce": nonce, "result": result_hash,
                },
            })
        except (ConnectionError, OSError, ssl.SSLError) as e:
            self._connected = False
            log.warning(f"Submit failed (connection lost): {e}")

    def keepalive(self):
        self._send({
            "id": self._next_id(), "jsonrpc": "2.0", "method": "keepalived",
            "params": {"id": self.session_id},
        })

    # ── background recv ──────────────────────────────────────────────────
    def set_job_callback(self, cb):
        self._job_cb = cb

    def start_recv_loop(self):
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    def _recv_loop(self):
        while self._running:
            try:
                line = self._recv_line()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("method") == "job":
                    jd = data.get("params", {})
                    self.current_job = Job(jd)
                    log.info(
                        f"New job  id={self.current_job.job_id}  "
                        f"h={self.current_job.height}  diff={self.current_job.target_difficulty}"
                    )
                    if self._job_cb:
                        self._job_cb(self.current_job)
                elif "result" in data:
                    r = data["result"]
                    if (isinstance(r, dict) and r.get("status") == "OK") or r == "OK":
                        self._accepted += 1
                        log.info(f"Share ACCEPTED  [{self._accepted}/{self._accepted+self._rejected}]")
                    else:
                        pass
                elif data.get("error"):
                    self._rejected += 1
                    log.warning(
                        f"Share REJECTED: {data['error']}  "
                        f"[{self._accepted}/{self._accepted+self._rejected}]"
                    )
            except ConnectionError:
                log.error("Connection lost")
                self._connected = False
                break
            except json.JSONDecodeError:
                continue
            except Exception as e:
                if self._running:
                    log.error(f"Recv error: {e}")

    def stop(self):
        self._running = False
        try:
            self._sock.close()
        except Exception:
            pass

    @property
    def accepted(self):
        return self._accepted

    @property
    def rejected(self):
        return self._rejected

    @property
    def connected(self):
        return self._connected
