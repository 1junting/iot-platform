"""隔離網路設備替身：原生 Modbus TCP 與 LwM2M CoAP 資源。"""
import asyncio
import json
import socketserver
import struct
import threading

import aiocoap
import aiocoap.resource


class Modbus(socketserver.BaseRequestHandler):
    def handle(self):
        raw = b''
        while len(raw) < 12:
            part = self.request.recv(12 - len(raw))
            if not part:
                return
            raw += part
        tx, proto, length, unit, function, address, count = struct.unpack('>HHHBBHH', raw)
        if function not in (3, 4) or address > 10 or count < 1 or count > 2:
            body = bytes([function | 128, 2])
        else:
            # register 0: 350 -> 35.0 C；register 1: 650 -> 65.0 %。
            registers = {0: 350, 1: 650}
            data = b''.join(struct.pack('>H', registers.get(address + i, 0)) for i in range(count))
            body = bytes([function, len(data)]) + data
        self.request.sendall(struct.pack('>HHHB', tx, proto, len(body) + 1, unit) + body)


class Sensor(aiocoap.resource.Resource):
    async def render_get(self, request):
        return aiocoap.Message(code=aiocoap.CONTENT, payload=b'35.0', content_format=0)


async def main():
    server = socketserver.ThreadingTCPServer(('0.0.0.0', 5020), Modbus)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = aiocoap.resource.Site()
    site.add_resource(['3303', '0', '5700'], Sensor())
    context = await aiocoap.Context.create_server_context(site, bind=('0.0.0.0', 5683), transports=['udp6'])
    while True:
        try:
            response = await context.request(aiocoap.Message(
                code=aiocoap.POST,
                uri='coap://exchange:5683/rd?ep=fixture-lwm2m&lt=120&lwm2m=1.0&b=U',
                payload=b'</3303/0>', content_format=40)).response
            print(json.dumps({'registration': str(response.code), 'location': list(response.opt.location_path)}), flush=True)
        except Exception as error:
            print(type(error).__name__, flush=True)
        await asyncio.sleep(30)


if __name__ == '__main__':
    asyncio.run(main())
