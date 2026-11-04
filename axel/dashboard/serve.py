"""Dev-only preview server for the AXEL dashboard draft: no-store responses
so edited JS modules are never served from the browser cache."""
import functools
import http.server
import socketserver
import sys

root = sys.argv[1]
port = int(sys.argv[2])

class NoStore(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # send_head's buffer is already flushed by then — headers must be
        # added before end_headers() writes the blank line
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

socketserver.ThreadingTCPServer.allow_reuse_address = True
with socketserver.ThreadingTCPServer(("", port), functools.partial(NoStore, directory=root)) as httpd:
    httpd.daemon_threads = True
    print(f"serving {root} at http://127.0.0.1:{port}/ (no-store)", flush=True)
    httpd.serve_forever()
