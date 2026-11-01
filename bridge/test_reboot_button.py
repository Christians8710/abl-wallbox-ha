"""Exercise real HTTP requests while replacing every real reboot operation."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
from threading import Thread
from unittest import TestCase, main
from unittest.mock import patch
import json
import subprocess

spec=spec_from_file_location('abl_bridge',Path(__file__).with_name('abl-bridge.py'))
bridge=module_from_spec(spec)
spec.loader.exec_module(bridge)
real_schedule=bridge.schedule_board_reboot

class RebootTests(TestCase):
    def setUp(self):
        bridge.status['reboot_pending']=False
        self.mock=patch.object(bridge,'schedule_board_reboot')
        self.schedule=self.mock.start()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),bridge.StatusPage)
        self.thread=Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.port=self.server.server_address[1]
        self.origin=f'http://127.0.0.1:{self.port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.mock.stop()

    def request(self,method='POST',path='/reboot',body=None,headers=None):
        if body is None: body=json.dumps({'confirm':True})
        standard={'Origin':self.origin,'Content-Type':'application/json','X-ABL-CSRF':bridge.reboot_token}
        standard.update(headers or {})
        connection=HTTPConnection('127.0.0.1',self.port,timeout=4)
        try:
            connection.request(method,path,body=body if method=='POST' else None,headers=standard)
            response=connection.getresponse()
            return response.status,response.read(),dict(response.getheaders())
        finally: connection.close()

    def test_page_and_status_keep_read_access(self):
        code,body,headers=self.request('GET','/')
        self.assertEqual(code,200)
        self.assertIn(b'Tinker Board neu starten',body)
        self.assertIn(b'confirm(',body)
        self.assertIn(b'waitForBoard',body)
        self.assertEqual(headers['X-Frame-Options'],'DENY')
        code,body,_=self.request('GET','/status.json')
        self.assertEqual(code,200)
        self.assertIn('boot_id',json.loads(body))
        self.assertNotIn(bridge.reboot_token,body.decode())
        self.schedule.assert_not_called()

    def test_get_cannot_reboot(self):
        self.assertEqual(self.request('GET')[0],404)
        self.schedule.assert_not_called()

    def test_missing_wrong_and_unicode_tokens_cannot_reboot(self):
        for token in ('','wrong','\N{LATIN SMALL LETTER A WITH DIAERESIS}'):
            # http.client encodes header values as Latin-1, matching the HTTP parser.
            self.assertEqual(self.request(headers={'X-ABL-CSRF':token})[0],403)
        self.schedule.assert_not_called()

    def test_cross_origin_cannot_reboot(self):
        self.assertEqual(self.request(headers={'Origin':'https://other.example'})[0],403)
        self.schedule.assert_not_called()

    def test_dns_rebinding_host_cannot_reboot(self):
        host=f'other.example:{self.port}'
        self.assertEqual(self.request(headers={'Host':host,'Origin':'http://'+host})[0],403)
        self.schedule.assert_not_called()

    def test_confirmation_and_body_must_be_valid(self):
        for body in ('{','[]','{"confirm":false}','{"confirm":"true"}'):
            self.assertEqual(self.request(body=body)[0],400)
        self.assertEqual(self.request(body='x'*1025)[0],413)
        self.assertEqual(self.request(headers={'Content-Type':'text/plain'})[0],415)
        self.schedule.assert_not_called()

    def test_valid_request_schedules_once_including_duplicate_clicks(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.request(),range(2)))
        self.assertEqual([value[0] for value in results],[202,202])
        self.schedule.assert_called_once_with()
        self.assertTrue(bridge.status['reboot_pending'])

    def test_failed_scheduler_allows_retry(self):
        self.schedule.side_effect=subprocess.CalledProcessError(1,['systemd-run'])
        self.assertEqual(self.request()[0],503)
        self.assertFalse(bridge.status['reboot_pending'])
        self.schedule.side_effect=None
        self.assertEqual(self.request()[0],202)

    def test_timer_uses_fixed_arguments_without_a_shell(self):
        with patch.object(bridge.subprocess,'run') as command:
            real_schedule()
        args,options=command.call_args
        self.assertEqual(args[0],['/usr/bin/systemd-run','--quiet','--collect','--unit=abl-bridge-board-reboot','--on-active=5s','/usr/bin/systemctl','reboot'])
        self.assertTrue(options['check'])
        self.assertNotIn('shell',options)

if __name__=='__main__':
    main(verbosity=2)
