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
  var pending = "";
  var heldLast = "";

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
    var path = document.getElementById("path");
    if (body.path && body.path.length) {
      var lines = body.path.map(function (p, index) {
        var label = index === 0 ? "from" : index === body.path.length - 1 ? "to" : "via";
        return label + "  " + p.map(function (n) { return Number(n).toFixed(2); }).join("  ");
      });
      if (body.clamped) lines.push("clamped into the flight zone");
      if (body.length_m != null) lines.push(Number(body.length_m).toFixed(2) + " m");
      path.textContent = lines.join("\n");
    } else {
      path.textContent = "";
    }
    readback.hidden = false;
  }

  function tapPoint(source, u, v) {
    if (role !== "controller") return;
    post("/api/preview_point", { source: source, u: u, v: v })
      .then(showReadback)
      .catch(function (err) { statusEl.textContent = err.message; });
  }

  cam.addEventListener("click", function (event) {
    var rect = cam.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    tapPoint("camera", (event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
  });

  var map = document.getElementById("map");
  map.addEventListener("click", function (event) {
    var rect = map.getBoundingClientRect();
    tapPoint("map", (event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
  });

  function drawMap() {
    fetch("/api/map", { headers: headers() }).then(function (res) {
      return res.json();
    }).then(function (body) {
      map.innerHTML = "";
      var label = document.createElement("div");
      label.className = "map-label";
      label.textContent = "JEM, top down. Red is keep-out.";
      map.appendChild(label);
      (body.keepouts || []).forEach(function (box) {
        var mark = document.createElement("div");
        mark.className = "keepout";
        mark.style.left = (box.u * 100) + "%";
        mark.style.top = (box.v * 100) + "%";
        mark.style.width = (box.w * 100) + "%";
        mark.style.height = (box.h * 100) + "%";
        map.appendChild(mark);
      });
    }).catch(function () {});
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
      drawMap();
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
    post("/api/confirm", { preview_id: previewId }).then(function (body) {
      readback.hidden = true;
      previewId = "";
      pending = body.about || body.action || "command";
      statusEl.textContent = "Moving. " + pending;
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
      var activity = body.activity;
      var line;
      if (activity && body.busy) {
        var what = activity.label || activity.action || "command";
        if (activity.step) what += " — " + activity.step + " " + activity.ack;
        line = "Moving. " + what;
        pending = "";
      } else if (pending && (body.busy || (body.last || "") === heldLast)) {
        // Confirmed, but this poll can arrive before the worker is marked busy.
        // Keep the command just sent until the previous summary actually changes.
        line = "Moving. " + pending;
      } else {
        pending = "";
        line = (body.busy ? "Moving. " : "Idle. ") + (body.last || "");
      }
      heldLast = body.last || "";
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
