"""Throwaway OpenAI-compatible stub for testing the cloud LLM path."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8899
seen = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                     "model": body.get("model"),
                     "has_images": any(
                         isinstance(m.get("content"), list)
                         for m in body.get("messages", []))})
        if self.path != "/v1/chat/completions":
            self.send_response(404); self.end_headers(); return
        text = ("Cloud note for " + body.get("model", "?") +
                ": the settlement is severely affected and road access is cut. "
                "Verify on the ground before dispatch.")
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out.encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(seen).encode())


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
