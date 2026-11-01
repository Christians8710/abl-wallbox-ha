// UART and HTTPServer API simulation; no hardware access.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function bridge() {
  let receive;
  let endpoint;
  let expire;
  const sent = [];
  const uart = {configure: c => assert.deepEqual(JSON.parse(JSON.stringify(c)), {baud:38400,format:'8E1'}),
    recv: callback => {receive=callback;}, send: data => sent.push(data)};
  const context = {UART:{get:()=>uart}, HTTPServer:{registerEndpoint:(name,fn)=>{assert.equal(name,'abl');endpoint=fn;}},
    Timer:{set:(ms,repeat,fn)=>{expire=fn;return 1;},clear:()=>{}}, console};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../shelly/abl-uart-bridge.js'),'utf8'),context);
  const response=()=>({code:200,body:'',sent:0,send(){this.sent++;}});
  return {sent,response,request:body=>{const res=response();endpoint({method:'POST',body},res);return res;},
    receive:data=>receive(data),expire:()=>expire()};
}

test('forwards valid ASCII and joins fragmented UART replies',()=>{
  const b=bridge();const reply=b.request(':010300010002F9\r\n');
  assert.equal(b.sent.length,1);b.receive('>01030401');b.receive('4141165F\r\n');
  assert.equal(reply.code,200);assert.equal(reply.body,'>010304014141165F\r\n');assert.equal(reply.sent,1);
});
test('rejects corrupt requests without UART transmission',()=>{
  const b=bridge();const reply=b.request(':010300010002F8\r\n');
  assert.equal(reply.code,400);assert.equal(b.sent.length,0);
});
test('rejects a second simultaneous request',()=>{
  const b=bridge();b.request(':010300010002F9\r\n');
  assert.equal(b.request(':010300010002F9\r\n').code,409);
});
test('times out a sleeping controller and accepts the next wakeup retry',()=>{
  const b=bridge();const first=b.request(':010300010002F9\r\n');b.expire();assert.equal(first.code,504);
  const second=b.request(':010300010002F9\r\n');b.receive('>010304014141165F\r\n');assert.equal(second.code,200);
});
test('accepts charging setpoint command',()=>{
  const b=bridge();const res=b.request(':0110001400010200A632\r\n');assert.equal(b.sent.length,1);
  b.receive('>011000140001DA\r\n');assert.equal(res.code,200);
});
