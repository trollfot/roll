import asyncio
from http import HTTPStatus
from urllib.parse import parse_qs

from httptools import parse_url, HttpRequestParser
from kua.routes import Routes, RouteError


class HttpError(Exception):
    ...


class Request:

    __slots__ = ('EOF', 'path', 'query_string', 'query', 'method', 'kwargs')

    def __init__(self):
        self.EOF = False
        self.kwargs = {}

    def on_url(self, url: bytes):
        parsed = parse_url(url)
        self.path = parsed.path.decode()
        self.query_string = (parsed.query or b'').decode()
        self.query = parse_qs(self.query_string)

    def on_message_complete(self):
        self.EOF = True


class Response:

    __slots__ = ('_status', 'headers', 'body')

    def __init__(self, body=b'', status=200, headers=None):
        self._status = None
        self.body = body
        self.status = status
        self.headers = headers or {}

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, code):
        status_ = HTTPStatus(code)
        self._status = '{} {}'.format(status_.value, status_.phrase).encode()


class Roll:

    requests_count = 0  # Needed by Gunicorn worker.

    def __init__(self):
        self.routes = Routes()
        self.hooks = {}

    async def startup(self):
        self.connections = {}  # Used by Gunicorn worker.
        await self.fire('startup')

    async def shutdown(self):
        pass

    async def __call__(self, reader, writer):
        chunks = 2 ** 16
        req = Request()
        parser = HttpRequestParser(req)
        while True:
            data = await reader.read(chunks)
            parser.feed_data(data)
            if not data or req.EOF:
                break
        req.method = parser.get_method().decode().upper()
        resp = await self.respond(req)
        self.write(writer, resp)

    async def respond(self, req):
        resp = await self.fire('request', request=req)
        if not resp:
            if req.method == 'OPTIONS':
                resp = b'', 200
            else:
                try:
                    # Both can return an HttpError.
                    params, handler = self.dispatch(req)
                    resp = await handler(req, **params)
                except HttpError as e:
                    resp = e.args[::-1]  # Allow to raise HttpError(status)
            if not isinstance(resp, (tuple, Response)):
                # Allow views to only return body.
                resp = (resp,)
            resp = Response(*resp)
        resp = await self.fire('response', response=resp, request=req) or resp
        if not isinstance(resp, Response):
            resp = Response(*resp)
        return resp

    def serve(self, port=3579, host='0.0.0.0'):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.startup())
        print("Rolling on %s:%d" % (host, port))
        loop.create_task(asyncio.start_server(self, host, port))
        loop.run_forever()
        loop.close()

    def write(self, writer, resp):
        writer.write(b'HTTP/1.1 %b\r\n' % resp.status)
        if not isinstance(resp.body, bytes):
            resp.body = resp.body.encode()
        if 'Content-Length' not in resp.headers:
            length = len(resp.body)
            resp.headers['Content-Length'] = str(length)
        for key, value in resp.headers.items():
            writer.write(b'%b: %b\r\n' % (key.encode(), str(value).encode()))
        writer.write(b'\r\n')
        writer.write(resp.body)
        writer.write_eof()

    def route(self, path, methods=None):
        if methods is None:
            methods = ['GET']

        def wrapper(func):
            self.routes.add(path, {m: func for m in methods})

        return wrapper

    def dispatch(self, req):
        try:
            params, handlers = self.routes.match(req.path)
        except RouteError:
            raise HttpError(404, req.path)
        if req.method not in handlers:
            raise HttpError(405, HTTPStatus(405))
        req.kwargs.update(params)
        return params, handlers[req.method]

    def listen(self, name):
        def wrapper(func):
            self.hooks.setdefault(name, [])
            self.hooks[name].append(func)
        return wrapper

    async def fire(self, name, **kwargs):
        try:
            for func in self.hooks[name]:
                await func(**kwargs)
        except KeyError:
            # Nobody registered to this event, let's roll anyway.
            pass
