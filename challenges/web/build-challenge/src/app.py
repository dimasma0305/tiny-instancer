from http.server import HTTPServer, SimpleHTTPRequestHandler

httpd = HTTPServer(('0.0.0.0', 8000), SimpleHTTPRequestHandler)
httpd.serve_forever()