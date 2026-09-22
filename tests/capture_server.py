#!/usr/bin/env python3
# 本地"假飞书"端点: 把收到的 POST body 落盘, 供测试断言
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

OUT = "/tmp/fw_capture"
os.makedirs(OUT, exist_ok=True)


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        name = self.path.strip("/").replace("/", "_") or "root"
        i = len(os.listdir(OUT))
        with open(os.path.join(OUT, "%s_%02d.json" % (name, i)), "wb") as f:
            f.write(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"code":0,"msg":"ok"}')

    def log_message(self, *a):
        pass


print("capture server on 127.0.0.1:9999 ->", OUT, flush=True)
HTTPServer(("127.0.0.1", 9999), H).serve_forever()
