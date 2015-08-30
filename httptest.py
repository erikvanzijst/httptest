"""A real live HTTP server to use in tests"""

import errno
import socket
import time
from io import BytesIO
from threading import Event, Thread
from wsgiref.handlers import format_date_time
from wsgiref.simple_server import (WSGIRequestHandler, WSGIServer,
                                   make_server as make_wsgi_server)

try:
    from urllib.parse import urljoin
except ImportError:
    from urlparse import urljoin

__all__ = ['testserver', 'TestRequest', 'TestResponse', 'TestServer',
           'TestServerError', 'TestServerTimeoutError']

class TestRequest(object):
    """A request made to the test server"""

    def __init__(self, method=None, protocol=None, address=None, path=None,
                 headers=None, body=None):
        self.method = method
        self.protocol = protocol
        self.address = address
        self.path = path
        self.headers = headers
        self.body = body

class TestResponse(object):
    """A response from the test server"""

    def __init__(self, status=None, headers=None, body=None):
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
    """Like WSGIRequestHandler, but disables logging"""

    def log_request(self, code='-', size='-'):
        pass

def _logmiddleware(app, log):
    """Wrap WSGI app with a middleware that logs request and response
    information to log.
    """
    def wrapper(environ, start_response):
        request = TestRequest()
        response = TestResponse()
        try:
            request.method = environ['REQUEST_METHOD']
            request.protocol = environ['SERVER_PROTOCOL']
            request.address = environ['REMOTE_ADDR']

            path = environ.get('SCRIPT_NAME') or '/'
            pathinfo = environ.get('PATH_INFO', '')
            if not environ.get('SCRIPT_NAME'):
                path += pathinfo[1:]
            else:
                path += pathinfo
            querystring = environ.get('QUERY_STRING')
            if querystring:
                path += '?' + querystring
            request.path = path

            rawlength = environ.get('CONTENT_LENGTH')
            if rawlength and rawlength.isdigit():
                length = int(rawlength)
            else:
                length = -1
            if length > 0:
                request.body = environ['wsgi.input'].read(length)
                environ['wsgi.input'] = BytesIO(request.body)
            else:
                request.body = None

            reqheaders = {}
            contenttype = environ.get('CONTENT_TYPE')
            if rawlength:
                reqheaders['content-length'] = rawlength
            if contenttype:
                reqheaders['content-type'] = contenttype
            for key, value in environ.items():
                if key.startswith('HTTP_'):
                    key = key[5:].replace('_', '-').lower()
                    reqheaders[key] = value
            request.headers = reqheaders

            resheaders = {}
            written = []
            def start_response_wrapper(status, headers, exc_info=None):
                write = start_response(status, headers, exc_info)

                response.status = status
                for key, value in headers:
                    key = key.lower()
                    if key in resheaders:
                        resheaders[key] += ',' + value
                    else:
                        resheaders[key] = value

                if 'date' not in resheaders:
                    date = format_date_time(time.time())
                    resheaders['date'] = date
                    headers.append(('Date', date))

                if 'server' not in resheaders:
                    server = 'httptest'
                    resheaders['server'] = server
                    headers.append(('Server', server))

                def write_wrapper(data):
                    written.append(data)
                    return write(data)
                return write_wrapper

            out = list(app(environ, start_response_wrapper))
            resbody = b''.join(written) + b''.join(out)
            if 'content-length' not in resheaders:
                resheaders['content-length'] = str(len(resbody))

            response.headers = resheaders
            response.body = resbody

            return out
        finally:
            log.append((request, response))
    return wrapper

def defaultapp(environ, start_response):
    start_response('204 No Content', [])
    return [b'']

def _makeserver(host, port, app, log, start, stop):
    httpd = make_wsgi_server(host, port, _logmiddleware(app, log),
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
        self._log = []
        self._stop = None
        self._timeout = timeout

    def start(self):
        """Start the HTTP server"""
        if self._httpd is None:
            port = self._startport
            for port in range(self._startport, self._startport + 100):
                if _portavailable(self._host, port):
                    break

            start = Event()
            self._port = port
            self._stop = Event()
            self._httpd = Thread(target=_makeserver,
                                 args=(self._host, self._port, self._app,
                                       self._log, start, self._stop))
            self._httpd.start()
            if not start.wait(self._timeout):
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
                raise TestServerTimeoutError('Timed out while stopping')
            else:
                self._httpd = None

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

# XXX: Support setting app as a string, 2-tuple (status, body), 3-tuple
#      (status, headers, body), or dict {url: string/2-tuple/3-tuple}.
#      The body should be a bytes object or an iterator that yields bytes.
#
# XXX: For dicts, how should 404s be handled? Should mapping None set
#      a catchall? Or should the mapping support globs?
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
    """
    return TestServer(app, host, startport, timeout)
