"""Tiny in-process SMTP server for tests (plain SMTP + AUTH PLAIN/LOGIN; no TLS). Never talks to the internet."""
import base64
import socketserver
import threading
from email import message_from_bytes, policy


class _Handler(socketserver.StreamRequestHandler):
    def w(self, s):
        self.wfile.write((s + "\r\n").encode()); self.wfile.flush()

    def handle(self):
        srv = self.server
        self.w("220 fake.smtp ready")
        mail_from, rcpt = None, []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd = line.decode("utf-8", "replace").strip()
            up = cmd.upper()
            if up.startswith(("EHLO", "HELO")):
                self.wfile.write(b"250-fake.smtp\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n"); self.wfile.flush()
            elif up.startswith("AUTH PLAIN"):
                parts = cmd.split()
                raw = parts[2] if len(parts) > 2 else (self.w("334 ") or self.rfile.readline().decode().strip())
                _, user, pw = base64.b64decode(raw).decode().split("\0")
                self._auth(user, pw)
            elif up.startswith("AUTH LOGIN"):
                self.w("334 VXNlcm5hbWU6"); user = base64.b64decode(self.rfile.readline().strip()).decode()
                self.w("334 UGFzc3dvcmQ6"); pw = base64.b64decode(self.rfile.readline().strip()).decode()
                self._auth(user, pw)
            elif up.startswith("MAIL FROM"):
                mail_from = cmd.split(":", 1)[1].strip().strip("<>").split()[0].strip("<>"); rcpt = []; self.w("250 OK")
            elif up.startswith("RCPT TO"):
                rcpt.append(cmd.split(":", 1)[1].strip().strip("<>")); self.w("250 OK")
            elif up == "DATA":
                self.w("354 go ahead")
                buf = b""
                while True:
                    l = self.rfile.readline()
                    if l in (b".\r\n", b".\n", b""):
                        break
                    buf += l[1:] if l.startswith(b"..") else l
                srv.messages.append({"from": mail_from, "to": rcpt, "msg": message_from_bytes(buf, policy=policy.default)})
                self.w("250 queued")
            elif up == "QUIT":
                self.w("221 bye"); return
            else:
                self.w("250 OK")

    def _auth(self, user, pw):
        srv = self.server
        if srv.creds and (user, pw) != srv.creds:
            self.w("535 bad credentials")
        else:
            srv.logins.append(user); self.w("235 ok")


class FakeSMTP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, creds=None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.messages, self.logins, self.creds = [], [], creds
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def close(self):
        self.shutdown(); self.server_close()
