// Voice entries: record with the microphone, then send the audio to this app
// (running on your own computer) to be transcribed. The text lands in the box.
(function () {
  const button = document.getElementById("record");
  const label = document.getElementById("record-label");
  const status = document.getElementById("record-status");
  const box = document.getElementById("entry-text");
  const csrf = document.querySelector("#entry-form input[name=csrf]").value;
  let recorder = null, chunks = [], timer = null, started = 0, alive = null, wakeLock = null;

  // While recording, ping the app every 4 minutes so the idle lock (20 min)
  // doesn't kick in during a long recording.
  function keepAlive() {
    const form = new FormData();
    form.append("csrf", csrf);
    fetch("/keepalive", { method: "POST", body: form, credentials: "same-origin" }).catch(() => {});
  }

  // Ask the phone to keep the screen on while recording; iPhones stop the
  // microphone when the screen locks. Not every browser supports this.
  async function holdScreen() {
    try { wakeLock = await navigator.wakeLock.request("screen"); } catch (e) { wakeLock = null; }
  }
  function releaseScreen() {
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  }

  if (!navigator.mediaDevices || !window.MediaRecorder) {
    button.disabled = true;
    status.textContent = "This browser can't record audio. Try Chrome, Edge, Firefox or Safari.";
  }

  function clock() {
    const s = Math.floor((Date.now() - started) / 1000);
    label.textContent = "Listening… " + Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0") + " · tap to stop";
  }

  async function start() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      status.textContent = "Microphone access was blocked. Allow it in your browser's site settings and try again.";
      return;
    }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      send(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
    };
    recorder.start();
    started = Date.now();
    clock();
    timer = setInterval(clock, 1000);
    alive = setInterval(keepAlive, 4 * 60 * 1000);
    holdScreen();
    button.classList.add("on");
    button.setAttribute("aria-pressed", "true");
    status.textContent = "Talk as long as you like. Keep the screen on, and tap again when you're done.";
  }

  function stop() {
    clearInterval(timer);
    clearInterval(alive);
    releaseScreen();
    button.classList.remove("on");
    button.setAttribute("aria-pressed", "false");
    button.disabled = true;
    label.textContent = "Turning it into text…";
    status.textContent = "Long recordings take a little while. The first time can take a few minutes while the speech model downloads.";
    recorder.stop();
  }

  async function send(blob) {
    const form = new FormData();
    form.append("csrf", csrf);
    form.append("audio", blob, "recording");
    try {
      const resp = await fetch("/transcribe", { method: "POST", body: form, credentials: "same-origin" });
      const data = await resp.json().catch(() => ({ error: "You were signed out. Reload the page and unlock again." }));
      if (data.error) {
        status.textContent = data.error;
      } else if (!data.text) {
        status.textContent = "I didn't catch anything. Try again a little closer to the mic.";
      } else {
        box.value = box.value.trim() ? box.value.trim() + "\n\n" + data.text : data.text;
        status.textContent = "Done. Check it over, fix anything that came out wrong, then save.";
        box.focus();
      }
    } catch (e) {
      status.textContent = "Couldn't reach the journal app. Is it still running?";
    }
    button.disabled = false;
    label.textContent = "Tap to talk";
    recorder = null;
  }

  button.addEventListener("click", () => (recorder ? stop() : start()));

  // Typing a long entry counts as activity too, so the idle lock doesn't throw
  // away a half-written entry when you finally press Save.
  let lastPing = Date.now();
  box.addEventListener("input", () => {
    if (Date.now() - lastPing > 4 * 60 * 1000) { lastPing = Date.now(); keepAlive(); }
  });

  document.querySelectorAll(".delete-form").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!confirm("Delete this entry for good?")) e.preventDefault();
    });
  });

  const summary = document.getElementById("summary-form");
  if (summary) {
    summary.addEventListener("submit", () => {
      const b = summary.querySelector("button");
      b.disabled = true;
      b.textContent = "Summarizing… (this can take a little while)";
    });
  }
})();
