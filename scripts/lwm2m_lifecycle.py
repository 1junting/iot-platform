"""原生 CoAP 註冊、更新、解除註冊與有效期驗收。"""
import asyncio
import json
import uuid
from pathlib import Path
import aiocoap
from exchange import db, sources


async def main():
    endpoint='lifecycle-'+uuid.uuid4().hex[:12]
    context=await aiocoap.Context.create_client_context(transports=['udp6'])
    try:
        response=await context.request(aiocoap.Message(code=aiocoap.POST,
            uri='coap://exchange:5683/rd?ep='+endpoint+'&lt=30&lwm2m=1.0&b=U',
            payload=b'</3303/0>',content_format=40)).response
        assert response.code==aiocoap.CREATED
        location='/'.join(response.opt.location_path)
        update=await context.request(aiocoap.Message(code=aiocoap.POST,uri='coap://exchange:5683/'+location+'?lt=1')).response
        assert update.code==aiocoap.CHANGED
        await asyncio.sleep(1.2)
        try:
            await sources.lwm2m_read({'endpoint':endpoint,'resources':[]})
        except ValueError as error:
            assert 'expired' in str(error)
        else:
            raise AssertionError('Expired registration accepted')
        removed=await context.request(aiocoap.Message(code=aiocoap.DELETE,uri='coap://exchange:5683/'+location)).response
        assert removed.code==aiocoap.DELETED
        with db.connect() as conn:
            assert conn.execute('SELECT 1 FROM registrations WHERE endpoint=%s',(endpoint,)).fetchone() is None
        report={'endpoint':endpoint,'register':str(response.code),'update':str(update.code),'expired_read_rejected':True,'deregister':str(removed.code)}
        Path('/reports/lwm2m-lifecycle.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report))
    finally:
        await context.shutdown()


asyncio.run(main())
