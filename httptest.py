"""A real live HTTP server to use in tests"""

import errno
import socket
from multiprocessing import Event, Process
from wsgiref.simple_server import (WSGIRequestHandler, WSGIServer,
                                   make_server as make_wsgi_server)

try:
    from urllib.parse import urljoin
except ImportError:
    from urlparse import urljoin

__all__ = ['testserver', 'TestServer', 'TestServerError',
           'TestServerTimeoutError']

class TestServerError(Exception):
    pass

class TestServerTimeoutError(TestServerError):
    pass

def _portavailable(host, port):
    """Check if the given host and port are available to be bound to"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, port))
        sock.close()
    except socket.error as e:
        if e.errno == errno.EADDRINUSE:
            return False
        raise
    else:
        return True

class _TestWSGIServer(WSGIServer):
    """Like WSGIServer, but sets a default timeout for handle_request()"""

    timeout = 0.5

class _TestWSGIRequestHandler(WSGIRequestHandler):
    """Like WSGIRequestHandler, but doesn't print requests to stderr"""

    def log_request(self, code='-', size='-'):
        pass

def _makeserver(app, port, start, stop):
    httpd = make_wsgi_server('localhost', port, app,
                             server_class=_TestWSGIServer,
                             handler_class=_TestWSGIRequestHandler)
    start.set()
    try:
        while not stop.is_set():
            httpd.handle_request()
    finally:
        httpd.server_close()

class TestServer(object):
    """A test HTTP server"""

    def __init__(self, app, host='localhost', startport=30059, timeout=30):
        self._app = app
        self._host = host
        self._startport = startport
        self._port = None
        self._httpd = None
        self._stop = None
        self._timeout = timeout

    def start(self):
        """Start the HTTP server"""
        if self._httpd is None:
            for port in range(self._startport, self._startport + 100):
                if _portavailable(self._host, port):
                    break

            start = Event()
            self._port = port
            self._stop = Event()
            self._httpd = Process(target=_makeserver, args=(self._app,
                                                            self._port,
                                                            start,
                                                            self._stop))
            self._httpd.start()
            if not start.wait(self._timeout):
                if self._httpd.is_alive():
                    self._httpd.terminate()
                raise TestServerTimeoutError('Timed out while starting')

    def __enter__(self):
        self.start()
        return self

    def stop(self):
        """Stop the HTTP server"""
        if self._httpd is not None:
            self._stop.set()
            self._httpd.join(self._timeout)
            if self._httpd.is_alive():
                self._httpd.terminate()
                raise TestServerTimeoutError('Timed out while stopping')

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()

    def url(self, path=None):
        """Generate a URL based on the HTTP server's host/port.

        Without any arguments, this returns the root URL (e.g.,
        'http://localhost:30059/'). If path is set, it will be joined to the
        root URL.
        """
        u = 'http://%s:%s/' % (self._host, self._port)
        if path is not None:
            u = urljoin(u, path)
        return u

def testserver(app, host='localhost', startport=30059, timeout=30):
    """Create a test HTTP server from a WSGI app.

    The test server will bind to the given host and start port (or the next
    available port up to one hundred).

    A timeout for starting/stopping the server can be specified. The default
    is 30 seconds.

    Usage:

    >>> def app(environ, start_response):
    ...     start_response('200 OK', [('Content-type', 'text/plain')])
    ...     return [b'Hello, test!']

    >>> import requests
    >>> with testserver(app) as server:
    ...     response = requests.get(server.url('/foo/bar'))

    >>> assert response.status_code == 200
    >>> assert response.headers['content-type'] == 'text/plain'
    >>> assert response.text == u'Hello, test!'

    Nesting is supported:

    >>> def app2(environ, start_response):
    ...     start_response('200 OK', [('Content-type', 'text/plain')])
    ...     return [b'Hello again!']

    >>> with testserver(app) as server1, testserver(app2) as server2:
    ...     response1 = requests.get(server1.url())
    ...     response2 = requests.get(server2.url())
    >>> assert response1.text == u'Hello, test!'
    >>> assert response2.text == u'Hello again!'

    As is manual starting/stopping:

    >>> server = testserver(app)
    >>> try:
    ...     server.start()
    ...     response = requests.get(server.url())
    ... finally:
    ...     server.stop()
    >>> assert response.text == u'Hello, test!'

    Note that on Windows the WSGI app will need to be importable (i.e.,
    it cannot be a closure and it must be pickleable).
    """
    return TestServer(app, host, startport, timeout)
