"""A real live HTTP server to use in tests"""

import errno
import socket
from io import BytesIO
from multiprocessing import Event, Process, Queue
from wsgiref.simple_server import (WSGIRequestHandler, WSGIServer,
                                   make_server as make_wsgi_server)

try:
    from queue import Empty
except ImportError:
    from Queue import Empty

try:
    from urllib.parse import urljoin
except ImportError:
    from urlparse import urljoin

__all__ = ['testserver', 'TestRequest', 'TestResponse', 'TestServer',
           'TestServerError', 'TestServerTimeoutError']

class TestRequest(object):
    """A request made to the test server"""

    def __init__(self, method, protocol, address, path, headers, body):
        self.method = method
        self.protocol = protocol
        self.address = address
        self.path = path
        self.headers = headers
        self.body = body

class TestResponse(object):
    """A response from the test server"""

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

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

class _TestWSGIRequestHandler(WSGIRequestHandler, object):
    """Like WSGIRequestHandler, but disables logging and captures extra
    request information.
    """

    def log_request(self, code='-', size='-'):
        pass

    def get_environ(self):
        env = super(_TestWSGIRequestHandler, self).get_environ()
        env['x-httptest.path'] = self.path
        env['x-httptest.headers'] = [tuple(line.rstrip().split(': ', 1))
                                     for line in self.headers.headers]
        return env

def _logmiddleware(app, logqueue):
    """Wrap WSGI app with a middleware that logs request and response
    information to logqueue.
    """
    def wrapper(environ, start_response):
        resstatus = [None]
        resheaders = [None]
        written = []
        def start_response_wrapper(status, headers, exc_info=None):
            resstatus[0] = status
            resheaders[0] = headers
            write = start_response(status, headers, exc_info)
            def write_wrapper(data):
                written.append(data)
                return write(data)
            return write_wrapper

        length = environ.get('CONTENT_LENGTH')
        if length and length.isdigit():
            length = int(length)
        else:
            length = -1
        if length > 0:
            reqbody = environ['wsgi.input'].read(length)
            environ['wsgi.input'] = BytesIO(reqbody)
        else:
            reqbody = None

        response = list(app(environ, start_response_wrapper))

        logqueue.put(({'method': environ['REQUEST_METHOD'],
                       'protocol': environ['SERVER_PROTOCOL'],
                       'address': environ['REMOTE_ADDR'],
                       'path': environ['x-httptest.path'],
                       'headers': environ['x-httptest.headers'],
                       'body': reqbody},
                      {'status': resstatus[0],
                       'headers': resheaders[0],
                       'body': ''.join(written) + ''.join(response)}))

        return response
    return wrapper

def defaultapp(environ, start_response):
    start_response('204 No Content', [])
    return ['']

def _makeserver(host, port, app, logqueue, start, stop):
    httpd = make_wsgi_server(host, port, _logmiddleware(app, logqueue),
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

    def __init__(self, app=defaultapp, host='localhost', startport=30059,
                 timeout=30):
        self._app = app
        self._host = host
        self._startport = startport
        self._port = None
        self._httpd = None
        self._logqueue = None
        self._log = []
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
            self._logqueue = Queue()
            self._stop = Event()
            self._httpd = Process(target=_makeserver,
                                  args=(self._host, self._port, self._app,
                                        self._logqueue, start, self._stop))
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
                self._httpd = None
                raise TestServerTimeoutError('Timed out while stopping')
            else:
                self._httpd = None

            while True:
                try:
                    reqdata, resdata = self._logqueue.get(timeout=1)
                except Empty:
                    break
                else:
                    self._log.append((TestRequest(**reqdata),
                                      TestResponse(**resdata)))

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

    def log(self):
        """Return a log of requests and responses"""
        return self._log

def testserver(app=defaultapp, host='localhost', startport=30059, timeout=30):
    """Create a test HTTP server from a WSGI app.

    The test server will bind to the given host and start port (or the next
    available port up to one hundred).

    A timeout for starting/stopping the server can be specified. The default
    is 30 seconds.

    Usage:

    >>> import requests
    >>> with testserver() as server:
    ...     response1 = requests.get(server.url('/foo'))
    ...     response2 = requests.get(server.url('/foo'))

    >>> assert len(server.log()) == 2
    >>> for request, response in server.log():
    ...     assert request.path == '/foo'
    ...     assert response.status == '204 No Content'


    A WSGI app can be provided:

    >>> def app(environ, start_response):
    ...     start_response('200 OK', [('Content-type', 'text/plain')])
    ...     return [b'Hello, test!']

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
