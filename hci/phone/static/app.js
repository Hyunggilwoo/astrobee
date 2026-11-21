(function () {
  "use strict";

  function encodeWav(samples, rate) {
    var buffer = new ArrayBuffer(44 + samples.length * 2);
    var view = new DataView(buffer);
    function write(offset, text) {
      for (var i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
    }
    write(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    write(8, "WAVE");
    write(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    write(36, "data");
    view.setUint32(40, samples.length * 2, true);
    var cursor = 44;
    for (var i = 0; i < samples.length; i++, cursor += 2) {
      var sample = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(cursor, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
  }

  var code = "";
  var clientId = "";
  var previewId = "";
  var role = "locked";

  var pair = document.getElementById("pair");
  var app = document.getElementById("app");
  var readback = document.getElementById("readback");
  var heard = document.getElementById("heard");
  var statusEl = document.getElementById("status");
  var poseEl = document.getElementById("pose");
  var roleEl = document.getElementById("role");
  var cam = document.getElementById("cam");
  var talkNote = document.getElementById("talk-note");

  function headers(extra) {
    var out = extra || {};
    if (code) out["X-Pairing-Code"] = code;
    if (clientId) out["X-Client-Id"] = clientId;
    return out;
  }

  function post(path, payload) {
    return fetch(path, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload || {})
    }).then(function (res) {
      return res.json().then(function (body) {
        if (!res.ok) throw new Error(body.error || ("HTTP " + res.status));
        return body;
      });
    });
  }

  function showReadback(body) {
    previewId = body.preview_id;
    heard.textContent = body.readback;
    readback.hidden = false;
  }

  document.getElementById("pair-form").addEventListener("submit", function (event) {
    event.preventDefault();
    code = document.getElementById("code").value.trim();
    post("/api/pair", { code: code }).then(function (body) {
      clientId = body.client_id;
      role = body.role;
      pair.hidden = true;
      app.hidden = false;
      document.getElementById("pair-error").textContent = "";
      poll();
    }).catch(function (err) {
      document.getElementById("pair-error").textContent = err.message;
    });
  });

  document.querySelectorAll("[data-command]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (role !== "controller") return;
      post("/api/preview", { text: button.getAttribute("data-command") })
        .then(showReadback)
        .catch(function (err) { statusEl.textContent = err.message; });
    });
  });

  document.getElementById("typed").addEventListener("submit", function (event) {
    event.preventDefault();
    var text = document.getElementById("typed-text").value;
    post("/api/preview", { text: text }).then(function (body) {
      document.getElementById("typed-text").value = "";
      showReadback(body);
    }).catch(function (err) { statusEl.textContent = err.message; });
  });

  document.getElementById("confirm").addEventListener("click", function () {
    post("/api/confirm", { preview_id: previewId }).then(function () {
      readback.hidden = true;
      previewId = "";
    }).catch(function (err) { statusEl.textContent = err.message; });
  });

  document.getElementById("cancel").addEventListener("click", function () {
    post("/api/cancel", { preview_id: previewId }).catch(function () {});
    readback.hidden = true;
    previewId = "";
  });

  document.getElementById("stop").addEventListener("click", function () {
    var tTap = Date.now();
    post("/api/stop", { t_tap: tTap }).then(function (body) {
      readback.hidden = true;
      var ms = body.tap_to_publish_ms;
      window.__stopNote = "STOP " + (ms == null ? "?" : Math.round(ms) + " ms") + ".";
    }).catch(function (err) { statusEl.textContent = err.message; });
  });

  function poll() {
    fetch("/api/status", { headers: headers() }).then(function (res) {
      return res.json();
    }).then(function (body) {
      role = body.role || role;
      roleEl.textContent = role;
      var pose = body.pose;
      poseEl.textContent = pose
        ? pose.position.map(function (n) { return Number(n).toFixed(2); }).join("  ")
        : "pose —";
      var line = (body.busy ? "Moving. " : "Idle. ") + (body.last || "");
      if (window.__stopNote) line += " " + window.__stopNote;
      statusEl.textContent = line;
      cam.src = "/api/camera.png?t=" + Date.now();
    }).catch(function () {});
    setTimeout(poll, 500);
  }

  var talk = document.getElementById("talk");
  if (!window.isSecureContext) {
    talk.disabled = true;
    talkNote.textContent = "Voice needs HTTPS (or localhost). Buttons work on plain HTTP.";
  } else {
    talkNote.textContent = "Push-to-talk uses the server Vosk grammar, then the same Confirm step.";
    var audio = null;
    var samples = [];
    talk.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      samples = [];
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        var ctx = new AudioContext();
        var source = ctx.createMediaStreamSource(stream);
        var node = ctx.createScriptProcessor(4096, 1, 1);
        node.onaudioprocess = function (item) {
          samples.push(new Float32Array(item.inputBuffer.getChannelData(0)));
        };
        source.connect(node);
        node.connect(ctx.destination);
        audio = { ctx: ctx, stream: stream, node: node, rate: ctx.sampleRate };
        talk.textContent = "Listening…";
      }).catch(function (err) { talkNote.textContent = err.message; });
    });
    function finish() {
      if (!audio) return;
      var taken = audio;
      audio = null;
      taken.node.disconnect();
      taken.stream.getTracks().forEach(function (track) { track.stop(); });
      taken.ctx.close();
      talk.textContent = "Hold to talk";
      var length = samples.reduce(function (n, chunk) { return n + chunk.length; }, 0);
      var merged = new Float32Array(length);
      var offset = 0;
      samples.forEach(function (chunk) { merged.set(chunk, offset); offset += chunk.length; });
      fetch("/api/voice", {
        method: "POST",
        headers: headers({ "Content-Type": "audio/wav" }),
        body: encodeWav(merged, taken.rate)
      }).then(function (res) {
        return res.json().then(function (body) { return { ok: res.ok, body: body }; });
      }).then(function (result) {
        if (!result.ok) throw new Error(result.body.error || "voice rejected");
        showReadback(result.body);
      }).catch(function (err) { talkNote.textContent = err.message; });
    }
    talk.addEventListener("pointerup", finish);
    talk.addEventListener("pointercancel", finish);
  }
})();
