# -*- coding: utf-8 -*-

import uuid
import uvloop
import asyncio
from roll import Roll, WSRoll
from roll import Response
from roll.extensions import logger, simple_server, traceback


asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


class HTMLResponse(Response):

    def html(self, body):
        self.headers['Content-Type'] = 'plain/text'
        self.body = body


class Basic(Roll):
    Response = HTMLResponse
        

class Application(WSRoll):
    Response = HTMLResponse


base = Basic()
app = Application()
logger(app)
traceback(app)
logger(base)
traceback(base)


@base.route('/')
@app.route('/')
async def hello(request, response):
    response.html('Hello World !')


@base.route('/feed')
@app.route('/feed', websocket=True)
async def feed(request, ws, **params):
    wsid = str(uuid.uuid4())
    await ws.send(f'Welcome {wsid} !')
    async for message in ws:
        for (task, socket) in request.app.websockets:
            if socket != ws:
                await socket.send('{}: {}'.format(wsid, message))


if __name__ == '__main__':
    simple_server(app)
