// ABL Wallbox UART bridge for Shelly Pro with RS485 Add-on. MIT License.
// Requires Serial mode "jsuart" and a stable firmware exposing UART and HTTPServer.
// Uses the Shelly device's existing HTTP authentication.
let uart = UART.get(0);
let pending = null;
let receiveBuffer = "";
let timer = null;

function finish(code, body) {
  if (timer !== null) { Timer.clear(timer); timer = null; }
  let reply = pending;
  pending = null;
  receiveBuffer = "";
  if (reply !== null) {
    reply.code = code;
    reply.body = body;
    reply.headers = [["Content-Type", "text/plain"]];
    reply.send();
  }
}

function validRequest(frame) {
  if (typeof frame !== "string" || frame.length < 17 || frame.length > 80) return false;
  if (frame.slice(0, 1) !== ":" || frame.slice(-2) !== "\r\n") return false;
  let hex = frame.slice(1, -2);
  if (hex.length % 2 !== 0) return false;
  let bytes = [];
  let sum = 0;
  for (let i = 0; i < hex.length; i += 2) {
    let pair = hex.slice(i, i + 2);
    if (!/^[0-9A-Fa-f]{2}$/.test(pair)) return false;
    let value = parseInt(pair, 16);
    bytes.push(value);
    sum += value;
  }
  if ((sum & 255) !== 0 || bytes[0] < 1 || bytes[0] > 16) return false;
  let register = bytes[2] * 256 + bytes[3];
  if (bytes[1] === 3) return bytes.length === 7 && bytes[4] === 0 && bytes[5] >= 1 && bytes[5] <= 8;
  if (bytes[1] !== 16 || bytes.length !== 10 || bytes[4] !== 0 || bytes[5] !== 1 || bytes[6] !== 2) return false;
  if (register !== 5 && register !== 20 && register !== 43 && register !== 44 && register !== 45) return false;
  if (register === 5) {
    let value = bytes[7] * 256 + bytes[8];
    return value === 0xA1A1 || value === 0xE0E0 || value === 0xE2E2 || value === 0x5A5A ||
      value === 0x4141 || value === 0x4040 || value === 0x3838 || value === 0x3030;
  }
  return true;
}

if (uart !== null) {
  uart.configure({baud: 38400, format: "8E1"});
  uart.recv(function(data) {
    if (pending === null) return;
    receiveBuffer += data;
    if (receiveBuffer.length > 1024) { finish(502, "UART response too large"); return; }
    let newline = receiveBuffer.indexOf("\n");
    while (newline >= 0) {
      let line = receiveBuffer.slice(0, newline + 1);
      receiveBuffer = receiveBuffer.slice(newline + 1);
      if (line.slice(0, 1) === ">") { finish(200, line); return; }
      newline = receiveBuffer.indexOf("\n");
    }
  });
}

HTTPServer.registerEndpoint("abl", function(request, response) {
  if (request.method !== "POST") { response.code = 405; response.body = "POST required"; response.send(); return; }
  if (uart === null) { response.code = 503; response.body = "UART unavailable: enable jsuart mode"; response.send(); return; }
  if (pending !== null) { response.code = 409; response.body = "UART busy"; response.send(); return; }
  if (!validRequest(request.body)) { response.code = 400; response.body = "Invalid ABL command"; response.send(); return; }
  pending = response;
  receiveBuffer = "";
  timer = Timer.set(1100, false, function() { finish(504, "No UART response"); });
  uart.send(request.body);
});
