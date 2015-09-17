"""A real live HTTP server to use in tests"""

import errno
import os
import select
import socket
import sys
import time
from collections import Iterable
from io import BytesIO
from threading import Event, Thread
from wsgiref.handlers import format_date_time
from wsgiref.simple_server import ServerHandler, WSGIRequestHandler, WSGIServer

try:
    from queue import Empty, Queue
except ImportError:
    from Queue import Empty, Queue

try:
    xrange
except NameError:
    xrange = range

__all__ = ['testserver', 'TestRequest', 'TestResponse', 'TestServer']

_reuseaddress = os.name != 'nt'

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

def _portavailable(host, port):
    """Check if the given host and port are available to be bound to"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if _reuseaddress:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        finally:
            sock.close()
    except socket.error as e:
        if e.errno == errno.EADDRINUSE:
            return False
        raise
    else:
        return True

class _TestWSGIServer(WSGIServer, object):
    """Like WSGIServer, but supports shutdown via pipe"""

    if os.name == 'nt':
        allow_reuse_address = _reuseaddress

    def __init__(self, *args, **kwargs):
        """Initialize the test HTTP server.

        In addition to the arguments WSGIserver accepts, this also
        requires a shutdownfd keyword argument. This should be a pipe file
        descriptor that the caller will use to communicate when the server
        should shut down.
        """
        self._shutdownfd = kwargs.pop('shutdownfd')
        super(_TestWSGIServer, self).__init__(*args, **kwargs)

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, socket.error) and e.errno == errno.EPIPE:
            return
        super(_TestWSGIServer, self).handle_error(request, client_address)

    def serve_forever(self, poll_interval=None):
        """Serve forever--or until told to shut down via the shutdownfd pipe"""
        if poll_interval is not None:
            raise ValueError('poll_interval is not supported')

        while True:
            while True:
                try:
                    fdsets = select.select([self, self._shutdownfd], [], [])
                    break
                except OSError as e:
                    if e.errno != errno.EINTR:
                        raise

            if self in fdsets[0]:
                self._handle_request_noblock()

            if self._shutdownfd in fdsets[0]:
                break

class _TestServerHandler(ServerHandler, object):
    """Like ServerHandler, but suppresses logging of EPIPE errors"""

    def handle_error(self):
        e = sys.exc_info()[1]
        if isinstance(e, socket.error) and e.errno == errno.EPIPE:
            return
        super(_TestServerHandler, self).handle_error()

class _TestWSGIRequestHandler(WSGIRequestHandler, object):
    """Like WSGIRequestHandler, but disables logging"""

    def log_request(self, code='-', size='-'):
        pass

    # This is implementation is unforunately copied from WSGIRequestHandler
    # so we can override the hardcoded ServerHandler class it uses.
    def handle(self):
        """Handle a single HTTP request"""
        self.raw_requestline = self.rfile.readline(65537)
        if len(self.raw_requestline) > 65536:
            self.requestline = ''
            self.request_version = ''
            self.command = ''
            self.send_error(414)
            return

        if not self.parse_request(): # An error code has been sent, just exit
            return

        handler = _TestServerHandler(
            self.rfile, self.wfile, self.get_stderr(), self.get_environ()
        )
        handler.request_handler = self      # backpointer for logging
        handler.run(self.server.get_app())

def _logmiddleware(app, logqueue):
    """Wrap WSGI app with a middleware that logs request and response
    information to logqueue.
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
            logqueue.put((request, response))
    return wrapper

def nocontent(environ, start_response):
    start_response('204 No Content', [])
    return [b'']

def _makeserver(host, port, app, logqueue, start, shutdownfd):
    httpd = _TestWSGIServer((host, port), _TestWSGIRequestHandler,
                            shutdownfd=shutdownfd)
    httpd.set_app(_logmiddleware(app, logqueue))
    start.set()
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()

_servertimeout = 5

class TestServer(object):
    """A test HTTP server"""

    def __init__(self, app=nocontent, host='localhost',
                 port=xrange(30059, 30159)):
        self._host = host
        self._log = []
        self._logqueue = Queue()
        self._shutdownpipe = None

        if isinstance(port, Iterable):
            p = None
            for p in port:
                if _portavailable(self._host, p):
                    break
            if p is not None:
                self._port = p
            else:
                raise ValueError('No port available in %r' % port)
        else:
            self._port = port

        start = Event()
        self._shutdownpipe = os.pipe()
        self._httpd = Thread(target=_makeserver,
                             args=(self._host, self._port, app,
                                   self._logqueue, start,
                                   self._shutdownpipe[0]))
        self._httpd.daemon = True
        self._httpd.start()
        if not start.wait(_servertimeout):
            raise RuntimeError('Timed out while starting %r' % self)

    def __enter__(self):
        return self

    def close(self):
        """Shut down the HTTP server"""
        if self._httpd is not None:
            os.write(self._shutdownpipe[1], b's')
            self._httpd.join(_servertimeout)
            if self._httpd.is_alive():
                raise RuntimeError('Timed out while shutting down %r' % self)
            else:
                self._httpd = None
                os.close(self._shutdownpipe[0])
                os.close(self._shutdownpipe[1])
                self._shutdownpipe = None

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def url(self, path='/'):
        """Generate a URL based on the HTTP server's host/port.

        Without any arguments, this returns the root URL (e.g.,
        'http://localhost:30059/'). If path is set, it will be joined to the
        root URL.
        """
        if not path.startswith('/'):
            path = '/' + path
        return 'http://%s:%s%s' % (self._host, self._port, path)

    def log(self):
        """Return a log of requests and responses"""
        while True:
            try:
                entry = self._logqueue.get(block=False)
            except Empty:
                break
            else:
                self._log.append(entry)

        return self._log

# XXX: Support setting app as a string, 2-tuple (status, body), 3-tuple
#      (status, headers, body), or dict {url: string/2-tuple/3-tuple}.
#      The body should be a bytes object or an iterator that yields bytes.
#
# XXX: For dicts, how should 404s be handled? Should mapping None set
#      a catchall? Or should the mapping support globs?
def testserver(app=nocontent, host='localhost', port=xrange(30059, 30159)):
    """Create a test HTTP server from a WSGI app.

    The test server will bind to the given host and port (or port range).

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

    As is manual starting/shutting down:

    >>> server = testserver(app)
    >>> try:
    ...     response = requests.get(server.url())
    ... finally:
    ...     server.close()
    >>> assert response.text == u'Hello, test!'
    """
    return TestServer(app, host, port)
