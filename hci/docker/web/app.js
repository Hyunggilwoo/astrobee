(function () {
  "use strict";

  var host = location.hostname || "localhost";
  var ros = new ROSLIB.Ros({ url: "ws://" + host + ":9090" });
  var conn = document.getElementById("conn");
  var cam = document.getElementById("cam");
  var camNote = document.getElementById("cam-note");
  var statusLine = document.getElementById("status-line");
  var ackList = document.getElementById("acks");
  var echoList = document.getElementById("echo");
  var novnc = document.getElementById("novnc");
  var COMPLETED = { 0: "NOT", 1: "OK", 2: "BAD_SYNTAX", 3: "EXEC_FAILED", 4: "CANCELED" };
  var PHASE = { 0: "QUEUED", 1: "EXECUTING", 2: "REQUEUED", 3: "COMPLETED" };
  var acks = [];

  novnc.href = "http://" + host + ":6080/vnc.html?autoconnect=1&resize=scale";
  novnc.textContent = host + ":6080";

  function setConn(text, kind) {
    conn.textContent = text;
    conn.className = "conn " + (kind || "");
  }

  ros.on("connection", function () {
    setConn("Rosbridge connected", "ok");
    startCamera();
  });
  ros.on("close", function () { setConn("Rosbridge closed", "bad"); });
  ros.on("error", function () { setConn("Rosbridge error", "bad"); });

  function num(value) {
    var n = Number(value);
    return Number.isFinite(n) ? n.toFixed(3) : "—";
  }

  new ROSLIB.Topic({
    ros: ros,
    name: "/loc/pose",
    messageType: "geometry_msgs/PoseStamped",
    throttle_rate: 400
  }).subscribe(function (msg) {
    var p = msg.pose.position;
    var q = msg.pose.orientation;
    document.getElementById("px").textContent = num(p.x);
    document.getElementById("py").textContent = num(p.y);
    document.getElementById("pz").textContent = num(p.z);
    document.getElementById("pq").textContent = [q.x, q.y, q.z, q.w].map(num).join(" ");
  });

  new ROSLIB.Topic({
    ros: ros,
    name: "/mgt/ack",
    messageType: "ff_msgs/AckStamped",
    queue_length: 20
  }).subscribe(function (msg) {
    var phase = PHASE[msg.status && msg.status.status] || String(msg.status && msg.status.status);
    var done = COMPLETED[msg.completed_status && msg.completed_status.status] || "?";
    acks.unshift({
      text: done + "  " + phase + "  " + (msg.cmd_id || "") + (msg.message ? "  " + msg.message : ""),
      ok: done === "OK" || done === "NOT"
    });
    acks = acks.slice(0, 40);
    ackList.innerHTML = "";
    acks.forEach(function (row) {
      var li = document.createElement("li");
      li.textContent = row.text;
      li.className = row.ok ? "ok" : "bad";
      ackList.appendChild(li);
    });
    document.getElementById("ack-count").textContent = "(" + acks.length + ")";
  });

  new ROSLIB.Topic({
    ros: ros,
    name: "/crew_assist/status",
    messageType: "std_msgs/String",
    throttle_rate: 400
  }).subscribe(function (msg) {
    var status;
    try { status = JSON.parse(msg.data); } catch (err) { return; }
    var busy = status.busy ? "busy" : "idle";
    statusLine.textContent = busy + (status.last ? "  ·  " + status.last : "");
    echoList.innerHTML = "";
    (status.lines || []).slice().reverse().forEach(function (line) {
      var li = document.createElement("li");
      li.textContent = line;
      echoList.appendChild(li);
    });
  });

  var intent = new ROSLIB.Topic({
    ros: ros,
    name: "/crew_assist/intent",
    messageType: "std_msgs/String"
  });

  function send(text, needsConfirm) {
    text = (text || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    if (needsConfirm && !window.confirm("Send this to the robot?\n\n" + text)) return;
    intent.publish(new ROSLIB.Message({ data: text }));
  }

  document.querySelectorAll("[data-command]").forEach(function (button) {
    button.addEventListener("click", function () {
      send(button.getAttribute("data-command"), button.getAttribute("data-confirm") === "1");
    });
  });

  document.getElementById("typed").addEventListener("submit", function (event) {
    event.preventDefault();
    var input = document.getElementById("typed-text");
    var text = input.value;
    var stop = /^(stop|halt|abort|freeze|emergency stop|stop all motion)$/i.test(text.trim());
    send(text, !stop);
    input.value = "";
  });

  function startCamera() {
    var stream = "http://" + host + ":8080/stream?topic=/hw/cam_nav&type=mjpeg&quality=70";
    var snap = "http://" + host + ":8080/snapshot?topic=/hw/cam_nav";
    var sawFrame = false;
    cam.addEventListener("load", function () {
      sawFrame = true;
      camNote.textContent = "";
    });
    camNote.textContent = "Opening /hw/cam_nav…";
    cam.src = stream;
    // Headless and some browsers drop the multipart stream. Snapshots still work.
    setTimeout(function () {
      if (sawFrame) return;
      camNote.textContent = "Showing nav-camera snapshots.";
      function tick() {
        cam.src = snap + "&t=" + Date.now();
      }
      tick();
      setInterval(tick, 1000);
    }, 4000);
  }
})();
